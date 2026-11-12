"""Motor de IA multimodal do ObraTrack AI (Gemini 2.5 Flash) com Modo Demonstração offline."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, PrivateAttr

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

MODELO = "gemini-2.5-flash"
PLACEHOLDER_CHAVE = "sua_chave_aqui"
SEM_ATRASO = "Sem atrasos registrados"

# Áudios até este tamanho seguem inline (Part.from_bytes); acima, vão pela Files API
# (o limite de payload inline do Gemini é ~20 MB somando todas as partes).
LIMITE_AUDIO_INLINE_BYTES = 15 * 1024 * 1024

# O `mimetypes` do Windows não conhece vários formatos de áudio de celular
# (ex.: .ogg/.opus do WhatsApp), por isso o mapeamento é explícito.
# O st.audio_input grava WAV, então o padrão é audio/wav.
MIME_AUDIO = {
    ".wav": "audio/wav",
    ".mp3": "audio/mp3",
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
            f"resguardo de prazo. Use exatamente '{SEM_ATRASO}' se não houver."
        )
    )
    mapeamento_asbuilt: str = Field(
        description=(
            "Análise técnica da foto identificando posição de tubulações, conduítes, "
            "fiação ou etapa da reforma, para histórico de manutenção futura."
        )
    )

    # Metadados fora do schema enviado ao Gemini (atributos privados não entram no JSON Schema).
    _modo_demo: bool = PrivateAttr(default=False)
    _aviso: str = PrivateAttr(default="")

    @property
    def modo_demo(self) -> bool:
        return self._modo_demo

    @property
    def aviso(self) -> str:
        return self._aviso


# --------------------------------------------------------------------------- #
# Chave de API
# --------------------------------------------------------------------------- #
def obter_api_key(api_key: Optional[str] = None) -> str:
    """Retorna a chave informada ou a do .env (relida a cada chamada), sem espaços."""
    if api_key and api_key.strip():
        chave = api_key.strip()
    else:
        load_dotenv(ENV_PATH, override=True)
        chave = (os.getenv("GEMINI_API_KEY") or "").strip()
    return "" if chave == PLACEHOLDER_CHAVE else chave


def salvar_api_key(api_key: str) -> None:
    """Grava/atualiza GEMINI_API_KEY no .env e na sessão atual."""
    from dotenv import set_key

    chave = api_key.strip()
    ENV_PATH.touch(exist_ok=True)
    set_key(ENV_PATH, "GEMINI_API_KEY", chave)
    os.environ["GEMINI_API_KEY"] = chave


def _erro_de_chave(exc: errors.APIError) -> bool:
    texto = f"{exc.status} {exc.message}".upper()
    return exc.code in (400, 401, 403) and (
        "API_KEY" in texto or "API KEY" in texto or exc.status in ("PERMISSION_DENIED", "UNAUTHENTICATED")
    )


# --------------------------------------------------------------------------- #
# Montagem do conteúdo multimodal
# --------------------------------------------------------------------------- #
def _montar_prompt(contexto_obra: str, observacoes_texto: str, tem_audio: bool, tem_foto: bool) -> str:
    fontes = []
    if tem_audio:
        fontes.append("um ÁUDIO com o relato do trabalhador (fonte principal)")
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
2. resumo_executivo: extraia do áudio (e complemente com foto e observações) os serviços executados e descreva-os, em linguagem técnica e profissional adequada ao cliente, considerando o escopo da obra. Não invente serviços que não foram relatados nem são visíveis.
3. nivel_produtividade: 'Alta', 'Média' ou 'Baixa', ponderando o volume entregue frente aos imprevistos e ao escopo.
4. motivo_atraso: extraia do relato chuvas, faltas de funcionários, falta de material ou problemas técnicos que impactaram o prazo e redija como justificativa formal (com horários, se citados). Se não houver, escreva exatamente '{SEM_ATRASO}'.
5. mapeamento_asbuilt: descreva tecnicamente o que a foto mostra — posição e trajeto de tubulações (água fria/quente, esgoto), conduítes, fiação, caixas, ralos, referências de medida (distância de piso, paredes, cantos) e a etapa da reforma — para consulta em manutenções futuras. Se não houver foto, use o que o áudio descrever sobre posições; sem nenhuma informação, escreva 'Sem foto para mapeamento As-Built'.
"""


def _parte_audio(client: genai.Client, caminho_audio: Path) -> tuple[types.Part | types.File, Optional[types.File]]:
    """Retorna a parte de áudio para o `contents` e, se houve upload, o arquivo remoto."""
    mime = MIME_AUDIO.get(caminho_audio.suffix.lower(), "audio/wav")
    audio_bytes = caminho_audio.read_bytes()

    if len(audio_bytes) <= LIMITE_AUDIO_INLINE_BYTES:
        return types.Part.from_bytes(data=audio_bytes, mime_type=mime), None

    # Relatos muito longos (> ~8 min em WAV 16 kHz) excedem o limite inline.
    arquivo = client.files.upload(file=caminho_audio, config=types.UploadFileConfig(mime_type=mime))
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


# --------------------------------------------------------------------------- #
# Modo Demonstração (offline)
# --------------------------------------------------------------------------- #
_REGRAS_ATRASO = [
    (r"chuv|chov|temporal|tempestade|garoa|alag", "Condições climáticas adversas (chuva) impediram ou reduziram os serviços externos/úmidos"),
    (r"falt\w*\s+(?:de\s+)?(?:\d+\s+|um\s+|uma\s+|o\s+|a\s+)?(?:pedreir|servent|ajudant|funcion|eletricist|encanador|pintor|azulejist|oper[aá]ri|equipe|m[aã]o de obra)",
     "Ausência de mão de obra prevista para o dia, reduzindo a capacidade produtiva da equipe"),
    (r"falt\w*\s+(?:de\s+)?(?:material|cimento|areia|argamassa|tijolo|bloco|piso|cano|tubo|conex|fio|cabo)|material\s+n[aã]o\s+(?:chegou|entregue)|atraso\s+(?:na|da)\s+entrega",
     "Falta ou atraso na entrega de materiais, impedindo a continuidade dos serviços"),
    (r"vazament|quebr|defeito|problema|imprevist|infiltra|rachadura|trinca|cano\s+estourad|sem\s+(?:energia|luz|[aá]gua)",
     "Problema técnico/imprevisto identificado em campo, exigindo correção antes da continuidade"),
]
_SINAIS_PARALISACAO = r"paramos|parou|paralis|suspens|n[aã]o\s+(?:trabalhamos|deu\s+para|foi\s+poss[ií]vel)|sem\s+trabalho"


def gerar_analise_demo(
    observacoes_texto: str = "",
    contexto_obra: str = "",
    tem_audio: bool = False,
    caminho_foto: Optional[Path] = None,
    aviso: str = "",
) -> AnaliseDiarioObra:
    """Gera uma análise estruturada por regras, sem chamar a API (testes de interface)."""
    obs = (observacoes_texto or "").strip()
    texto = obs.lower()

    causas = [descricao for padrao, descricao in _REGRAS_ATRASO if re.search(padrao, texto)]
    paralisou = bool(re.search(_SINAIS_PARALISACAO, texto))

    if causas:
        motivo = "; ".join(causas) + "."
        horarios = re.findall(r"\b\d{1,2}\s*(?:h|:\d{2})\s*(?:\d{2})?\b", obs)
        if horarios:
            motivo += f" Horários citados no relato: {', '.join(h.strip() for h in horarios)}."
        motivo += f" Relato original: “{obs}”."
    else:
        motivo = SEM_ATRASO

    if paralisou or len(causas) >= 2:
        nivel = "Baixa"
    elif causas or not obs:
        nivel = "Média"
    else:
        nivel = "Alta"

    escopo = next(
        (linha.split(":", 1)[1].strip() for linha in contexto_obra.splitlines() if linha.lower().startswith("escopo")),
        "",
    )
    if obs:
        resumo = f"Registro do dia: {obs[0].upper()}{obs[1:]}"
        resumo += "" if resumo.endswith(".") else "."
    else:
        resumo = "Registro do dia efetuado sem descrição textual dos serviços executados."
    if escopo and escopo.lower() != "não informado":
        resumo += f" Serviços enquadrados no escopo contratado: {escopo}"

    if caminho_foto:
        try:
            with Image.open(caminho_foto) as img:
                dims = f"{img.width}×{img.height} px"
        except Exception:
            dims = "dimensões indisponíveis"
        asbuilt = (
            f"Foto do dia arquivada como evidência As-Built ({dims}). A identificação automática de "
            "tubulações, conduítes e fiação requer a análise de IA (configure a GEMINI_API_KEY)."
        )
    else:
        asbuilt = "Sem foto para mapeamento As-Built"

    transcricao = (
        "[Modo Demonstração] Áudio gravado e arquivado no registro; a transcrição automática "
        "requer a GEMINI_API_KEY. Ouça o relato original no player."
        if tem_audio
        else ""
    )

    analise = AnaliseDiarioObra(
        transcricao_audio=transcricao,
        resumo_executivo=f"[Modo Demonstração] {resumo}",
        nivel_produtividade=nivel,
        motivo_atraso=motivo,
        mapeamento_asbuilt=asbuilt,
    )
    analise._modo_demo = True
    analise._aviso = aviso or "Análise gerada em Modo Demonstração (offline), sem IA."
    return analise


# --------------------------------------------------------------------------- #
# Função principal
# --------------------------------------------------------------------------- #
def processar_registro_obra(
    caminho_audio: Optional[str | Path] = None,
    caminho_foto: Optional[str | Path] = None,
    observacoes_texto: str = "",
    contexto_obra: str = "",
    api_key: Optional[str] = None,
    modo_demo: bool = False,
) -> AnaliseDiarioObra:
    """Analisa áudio + foto + observações de um dia de obra e devolve a análise validada.

    Cai no Modo Demonstração quando `modo_demo=True`, quando não há chave ou quando o
    Gemini rejeita a chave — assim o registro nunca deixa de ser salvo durante testes.
    """
    audio_path = Path(caminho_audio) if caminho_audio else None
    foto_path = Path(caminho_foto) if caminho_foto else None
    observacoes_texto = (observacoes_texto or "").strip()

    for p in (audio_path, foto_path):
        if p is not None and not p.is_file():
            raise FileNotFoundError(f"Arquivo não encontrado: {p}")
    if not (audio_path or foto_path or observacoes_texto):
        raise ValueError("Envie pelo menos um áudio, uma foto ou observações em texto.")

    def demo(aviso: str = "") -> AnaliseDiarioObra:
        return gerar_analise_demo(observacoes_texto, contexto_obra, audio_path is not None, foto_path, aviso)

    if modo_demo:
        return demo("Modo Demonstração ativado nas Configurações da IA.")

    chave = obter_api_key(api_key)
    if not chave:
        return demo("GEMINI_API_KEY não configurada — análise gerada em Modo Demonstração.")

    client = genai.Client(api_key=chave)
    contents: list = []
    arquivo_remoto: Optional[types.File] = None

    try:
        if audio_path:
            parte_audio, arquivo_remoto = _parte_audio(client, audio_path)
            contents.append(parte_audio)
        if foto_path:
            contents.append(_carregar_foto(foto_path))
        contents.append(
            _montar_prompt(contexto_obra, observacoes_texto, tem_audio=audio_path is not None, tem_foto=foto_path is not None)
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
    except errors.APIError as exc:
        if _erro_de_chave(exc):
            return demo("A GEMINI_API_KEY foi rejeitada pelo Google — análise gerada em Modo Demonstração.")
        raise
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
