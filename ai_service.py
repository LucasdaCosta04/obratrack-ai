"""Motor de IA multimodal do ObraTrack AI (Gemini 2.5 Flash)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

MODELO = "gemini-2.5-flash"

# Áudios acima deste tamanho vão pela Files API; abaixo, seguem inline na requisição
# (o limite de payload inline do Gemini é ~20 MB somando todas as partes).
LIMITE_AUDIO_INLINE_BYTES = 15 * 1024 * 1024

# O módulo `mimetypes` do Windows não conhece vários formatos de áudio de celular
# (ex.: .ogg/.opus do WhatsApp), por isso o mapeamento é explícito.
MIME_AUDIO = {
    ".mp3": "audio/mp3",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".m4a": "audio/aac",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".aiff": "audio/aiff",
    ".webm": "audio/webm",
}


class AnaliseDiarioObra(BaseModel):
    """Saída estruturada da análise de um registro diário de obra."""

    transcricao_audio: str = Field(
        description="Transcrição fiel do relato do trabalhador. Vazio se não houver áudio."
    )
    resumo_executivo: str = Field(
        description=(
            "Atividades concluídas no dia, descritas de forma técnica e profissional "
            "para apresentar ao cliente."
        )
    )
    nivel_produtividade: Literal["Alta", "Média", "Baixa"] = Field(
        description="Avaliação das entregas do dia versus os imprevistos ocorridos."
    )
    motivo_atraso: str = Field(
        description=(
            "Detalhamento de chuvas, faltas de funcionários ou problemas técnicos, para "
            "resguardo de prazo. Use exatamente 'Sem atrasos registrados' se não houver."
        )
    )
    mapeamento_asbuilt: str = Field(
        description=(
            "Análise técnica da foto identificando posição de tubulações, conduítes, "
            "fiação ou etapa da reforma, para histórico de manutenção futura."
        )
    )


def _montar_prompt(
    contexto_obra: str,
    observacoes_texto: str,
    tem_audio: bool,
    tem_foto: bool,
) -> str:
    fontes = []
    if tem_audio:
        fontes.append("um ÁUDIO com o relato do trabalhador")
    if tem_foto:
        fontes.append("uma FOTO do serviço executado")
    if observacoes_texto:
        fontes.append("OBSERVAÇÕES escritas")
    fontes_txt = ", ".join(fontes) if fontes else "nenhuma mídia (apenas o contexto)"

    return f"""Você é um engenheiro civil responsável pelo diário de obra e pela documentação As-Built.

CONTEXTO DA OBRA:
{contexto_obra or "Não informado."}

OBSERVAÇÕES ESCRITAS DO DIA:
{observacoes_texto or "Nenhuma."}

Você recebeu: {fontes_txt}.

Instruções:
1. transcricao_audio: transcreva o áudio de forma fiel, em português, sem resumir nem corrigir o sentido. Se não houver áudio, retorne string vazia.
2. resumo_executivo: cruze o relato, a foto e as observações com o escopo da obra e descreva, em linguagem técnica e profissional adequada ao cliente, as atividades concluídas no dia. Não invente serviços que não foram relatados nem são visíveis.
3. nivel_produtividade: 'Alta', 'Média' ou 'Baixa', ponderando o volume entregue frente aos imprevistos e ao escopo.
4. motivo_atraso: detalhe objetivamente chuvas, faltas de funcionários, falta de material ou problemas técnicos que impactaram o prazo, de forma que sirva como justificativa formal. Se não houver, escreva exatamente 'Sem atrasos registrados'.
5. mapeamento_asbuilt: descreva tecnicamente o que a foto mostra — posição e trajeto de tubulações (água fria/quente, esgoto), conduítes, fiação, caixas, ralos, referências de medida (distância de piso, paredes, cantos) e a etapa da reforma — para consulta em manutenções futuras. Se não houver foto, escreva 'Sem foto para mapeamento As-Built'.
"""


def _carregar_audio(client: genai.Client, caminho_audio: Path) -> tuple[types.Part | types.File, Optional[types.File]]:
    """Retorna a parte de áudio para o `contents` e, se houve upload, o arquivo remoto."""
    mime = MIME_AUDIO.get(caminho_audio.suffix.lower())
    if mime is None:
        raise ValueError(
            f"Formato de áudio não suportado: {caminho_audio.suffix!r}. "
            f"Use um de: {', '.join(sorted(MIME_AUDIO))}."
        )

    if caminho_audio.stat().st_size <= LIMITE_AUDIO_INLINE_BYTES:
        return types.Part.from_bytes(data=caminho_audio.read_bytes(), mime_type=mime), None

    arquivo = client.files.upload(
        file=caminho_audio, config=types.UploadFileConfig(mime_type=mime)
    )
    # A Files API processa o arquivo de forma assíncrona; aguarda ficar ACTIVE.
    prazo = time.monotonic() + 120
    while arquivo.state == types.FileState.PROCESSING:
        if time.monotonic() > prazo:
            raise TimeoutError("Tempo esgotado aguardando o processamento do áudio no Gemini.")
        time.sleep(2)
        arquivo = client.files.get(name=arquivo.name)
    if arquivo.state == types.FileState.FAILED:
        raise RuntimeError("O Gemini falhou ao processar o arquivo de áudio enviado.")
    return arquivo, arquivo


def _carregar_foto(caminho_foto: Path) -> Image.Image:
    """Abre a foto corrigindo a rotação EXIF (comum em fotos de celular)."""
    with Image.open(caminho_foto) as img:
        img.load()
        return ImageOps.exif_transpose(img).convert("RGB")


def processar_registro_obra(
    caminho_audio: Optional[str | Path] = None,
    caminho_foto: Optional[str | Path] = None,
    observacoes_texto: str = "",
    contexto_obra: str = "",
) -> AnaliseDiarioObra:
    """Analisa áudio + foto + observações de um dia de obra e devolve a análise validada."""
    audio_path = Path(caminho_audio) if caminho_audio else None
    foto_path = Path(caminho_foto) if caminho_foto else None
    observacoes_texto = (observacoes_texto or "").strip()

    for p in (audio_path, foto_path):
        if p is not None and not p.is_file():
            raise FileNotFoundError(f"Arquivo não encontrado: {p}")
    if not (audio_path or foto_path or observacoes_texto):
        raise ValueError("Envie pelo menos um áudio, uma foto ou observações em texto.")

    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError(
            "GEMINI_API_KEY não configurada. Copie .env.example para .env e preencha a chave."
        )

    client = genai.Client()
    contents: list = []
    arquivo_remoto: Optional[types.File] = None

    try:
        if audio_path:
            parte_audio, arquivo_remoto = _carregar_audio(client, audio_path)
            contents.append(parte_audio)
        if foto_path:
            contents.append(_carregar_foto(foto_path))
        contents.append(
            _montar_prompt(
                contexto_obra,
                observacoes_texto,
                tem_audio=audio_path is not None,
                tem_foto=foto_path is not None,
            )
        )

        response = client.models.generate_content(
            model=MODELO,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=AnaliseDiarioObra,
                temperature=0.2,
            ),
        )
    finally:
        if arquivo_remoto is not None:
            try:
                client.files.delete(name=arquivo_remoto.name)
            except Exception:
                pass  # limpeza best-effort; o Gemini expira arquivos em 48h

    if isinstance(response.parsed, AnaliseDiarioObra):
        return response.parsed
    if not response.text:
        raise RuntimeError("O Gemini retornou uma resposta vazia (possível bloqueio de segurança).")
    return AnaliseDiarioObra.model_validate_json(response.text)
