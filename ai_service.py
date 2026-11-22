"""Motor de IA multimodal do ObraTrack AI (Gemini) com cadeia de modelos e análise offline.

Segurança: a chave da API é lida EXCLUSIVAMENTE do ambiente do servidor (variável de
ambiente ou arquivo .env). Ela nunca é recebida pela interface, e nenhuma mensagem
destinada ao usuário final menciona chave, variáveis de ambiente ou configurações.
Detalhes técnicos de falhas vão apenas para o log do servidor.
"""

from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import tempfile
import time
import warnings
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, PrivateAttr, ValidationError

from database import (
    AMBIENTES_EXTERNOS,
    ASBUILT_FOTO_SEM_DESCRICAO,
    SEM_FOTO_ASBUILT,
    ambientes_citados,
    corrigir_ambiente,
    eh_servico_interno,
    normalizar_busca,
)

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

log = logging.getLogger("obratrack.ai")

# Ordem de tentativa. Modelos inexistentes/descontinuados (404) são pulados sem espera.
# Família 3.x primeiro (validada na conta em 12/11/2026: 3.6 e 3.5 respondendo enquanto
# 3.8/3.7 retornavam 503); a família 2.x fica como último recurso — hoje responde 404
# para contas novas. O administrador pode sobrescrever a ordem com a variável de
# ambiente OBRATRACK_MODELOS (lista separada por vírgula).
MODELOS_PADRAO = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
]


def _modelos() -> list[str]:
    load_dotenv(ENV_PATH)
    personalizados = [m.strip() for m in os.environ.get("OBRATRACK_MODELOS", "").split(",") if m.strip()]
    return personalizados or MODELOS_PADRAO
TENTATIVAS_POR_MODELO = 2       # tentativas no mesmo modelo para falhas rápidas (503/429)
ESPERA_RETRY_SEGUNDOS = 2
TIMEOUT_REQUISICAO_MS = 60_000
# 503/429 falham na hora: vale repetir o mesmo modelo após 2 s. Qualquer outro erro
# (404, 500, 504/timeout, que já consumiu o tempo limite) troca direto de modelo.
CODIGOS_RETRY_MESMO_MODELO = {429, 503}

PLACEHOLDER_CHAVE = "sua_chave_aqui"
SEM_ATRASO = "Sem atrasos registrados"
SEM_PARTICULARIDADES = "Execução sem particularidades relatadas"
TRANSCRICAO_INDISPONIVEL = (
    "Transcrição automática indisponível para este registro. Ouça o relato original no "
    "player ou edite o registro para incluir a transcrição."
)

# Reconhecimento de fala dedicado (SpeechRecognition + serviço gratuito de voz do Google).
# Todo áudio é convertido para WAV PCM 16 kHz mono (ffmpeg do imageio-ffmpeg, via pydub) e
# dividido em trechos de até 35 s — o serviço recusa/ignora áudios longos numa só requisição.
# O corte é feito num silêncio perto do fim do trecho, para não partir palavras.
TRECHO_STT_MS = 35_000
JANELA_CORTE_MS = 8_000          # procura silêncio nos últimos 8 s de cada trecho
SILENCIO_MIN_MS = 350
TAXA_STT_HZ = 16_000
TIMEOUT_STT_SEGUNDOS = 30
AVISO_ANALISE_SIMPLIFICADA = (
    "A análise automática não está disponível no momento. O registro foi salvo com uma "
    "análise simplificada — você pode revisá-lo e editá-lo no Histórico."
)

# Áudios até este tamanho seguem inline (Part.from_bytes); acima, vão pela Files API
# (o limite de payload inline do Gemini é ~20 MB somando todas as partes).
LIMITE_AUDIO_INLINE_BYTES = 15 * 1024 * 1024

# O `mimetypes` do Windows não conhece vários formatos de áudio de celular
# (ex.: .ogg/.opus do WhatsApp). O st.audio_input grava WAV, então o padrão é audio/wav.
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


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class ItemTarefa(BaseModel):
    """Uma atividade individual, em um único ambiente/cômodo."""

    ambiente: str = Field(
        description="Cômodo ou área da obra (ex.: 'Banheiro Social', 'Cozinha', 'Muro'). Use 'Geral' se não for citado."
    )
    descricao: str = Field(description="Título curto da tarefa para o checklist (ex.: 'Retirada da cerâmica antiga').")
    resumo_servico: str = Field(
        description=(
            "O que foi explicado no áudio especificamente sobre ESTE serviço/ambiente, em linguagem técnica. "
            "Nunca inclua outros serviços ou cômodos falados no mesmo relato."
        )
    )
    detalhes_servico: str = Field(
        description=(
            "Detalhes técnicos de execução e particularidades DESTE serviço (materiais, medidas, dificuldades "
            f"do terreno/estrutura). Sem detalhes, escreva '{SEM_PARTICULARIDADES}'."
        )
    )
    concluida: bool = Field(
        description="True se o relato diz que foi feito hoje; False se diz que ainda falta fazer ou ficou pendente."
    )
    foto_indices: list[int] = Field(
        default_factory=list,
        description=(
            "Índices (começando em 0: Imagem 0, Imagem 1...) SOMENTE das imagens que mostram visualmente "
            "ESTA tarefa. Lista vazia se nenhuma imagem mostrar esta tarefa. Nunca inclua uma imagem só "
            "por ser do mesmo cômodo."
        ),
    )


class ItemFoto(BaseModel):
    """Classificação de uma foto enviada no registro (na ordem de envio)."""

    indice_foto: int = Field(description="Índice da imagem na ordem enviada, começando em 0 (Imagem 0, Imagem 1...).")
    ambiente: str = Field(description="Cômodo mostrado/relacionado à foto (ex.: 'Banheiro Social', 'Cozinha').")
    titulo_servico: str = Field(
        description="Serviço documentado na foto, curto e com o cômodo (ex.: 'Retirada da cerâmica do banheiro')."
    )
    descricao_tecnica: str = Field(
        description="O que a foto mostra: tubulações, conduítes, fiação, medidas de referência e etapa da reforma."
    )


class AnaliseDiarioObra(BaseModel):
    """Saída estruturada da análise de um registro diário de obra."""

    transcricao_audio: str = Field(
        description="Transcrição literal e completa, em português, de tudo o que foi falado no áudio. Vazio se não houver áudio."
    )
    resumo_executivo: str = Field(
        description="Atividades concluídas no dia, descritas de forma técnica e profissional para apresentar ao cliente."
    )
    detalhes_execucao_imprevistos: str = Field(
        description=(
            "Resumo das particularidades técnicas do serviço, dificuldades do terreno/estrutura, "
            "clima ou justificativas de prazo. Factual, sem dar nota ao dia."
        )
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
    tarefas_extraidas: list[ItemTarefa] = Field(
        default_factory=list,
        description="Cada atividade mencionada, em um bloco separado por ambiente/cômodo.",
    )
    fotos_classificadas: list[ItemFoto] = Field(
        default_factory=list,
        description="Uma entrada por foto recebida, indicando ambiente e serviço de cada uma. Vazio se não houver foto.",
    )

    # Metadados fora do schema enviado ao Gemini (atributos privados não entram no JSON Schema).
    _modo_offline: bool = PrivateAttr(default=False)
    _aviso: str = PrivateAttr(default="")
    _modelo: str = PrivateAttr(default="")

    @property
    def modo_offline(self) -> bool:
        """True quando a análise foi gerada por regras locais (sem IA)."""
        return self._modo_offline

    @property
    def aviso(self) -> str:
        """Mensagem neutra para o usuário final (nunca contém detalhes técnicos)."""
        return self._aviso

    @property
    def modelo(self) -> str:
        return self._modelo


# --------------------------------------------------------------------------- #
# Configuração do servidor (nunca exposta à interface)
# --------------------------------------------------------------------------- #
def _obter_api_key() -> str:
    """Lê a chave do Gemini apenas do ambiente do servidor.

    Returns:
        A chave, ou "" se ausente ou igual ao placeholder do .env.example.

    Note:
        Segurança: a variável de ambiente tem prioridade sobre o .env, e a chave nunca é
        pedida ao usuário nem enviada ao navegador.
    """
    load_dotenv(ENV_PATH)
    chave = (os.environ.get("GEMINI_API_KEY") or "").strip()
    return "" if chave == PLACEHOLDER_CHAVE else chave


def modo_offline_forcado() -> bool:
    """Permite ao administrador forçar a análise offline (ex.: testes) via OBRATRACK_MODO_OFFLINE=1."""
    load_dotenv(ENV_PATH)
    return os.environ.get("OBRATRACK_MODO_OFFLINE", "").strip().lower() in ("1", "true", "sim")


def _erro_de_chave(exc: errors.APIError) -> bool:
    texto = f"{exc.status} {exc.message}".upper()
    return exc.code in (400, 401, 403) and (
        "API_KEY" in texto or "API KEY" in texto or exc.status in ("PERMISSION_DENIED", "UNAUTHENTICATED")
    )


def _modelo_indisponivel(exc: errors.APIError) -> bool:
    return exc.code == 404 or exc.status == "NOT_FOUND"


# --------------------------------------------------------------------------- #
# Montagem do conteúdo multimodal
# --------------------------------------------------------------------------- #
INSTRUCAO_SISTEMA = f"""Você é um engenheiro civil responsável pelo diário de obra e pela documentação As-Built de pequenas reformas.
Responda sempre em português do Brasil e preencha TODOS os campos do schema.

Regras obrigatórias:
1) transcricao_audio: transcrição LITERAL e COMPLETA de tudo o que foi falado no áudio, do início ao fim, sem resumir, sem omitir trechos e sem corrigir o sentido (mantenha gírias e termos de obra). Se o prompt trouxer uma TRANSCRIÇÃO LITERAL DO ÁUDIO, use-a como base (pode apenas pontuar e corrigir palavras claramente mal reconhecidas, ouvindo o áudio). Sem áudio: string vazia.
   Todos os demais campos (resumo, detalhes, tarefas, ambientes) devem refletir FIELMENTE o que foi falado: cada serviço citado no áudio vira um item em tarefas_extraidas; não invente nem omita serviços.
2b) SEPARAÇÃO DE SERVIÇOS: quando o mesmo relato fala de serviços em ambientes diferentes (ex.: banheiro/cerâmica/encanamento E vala/fossa/anéis/sumidouro), gere itens SEPARADOS — um com ambiente "Banheiro" e outro com ambiente "Área Externa". Em cada item, resumo_servico e detalhes_servico falam SOMENTE daquele serviço/ambiente (nada da vala no item do banheiro e vice-versa). O resumo_executivo do dia cobre tudo o que foi explicado.
2) tarefas_extraidas: quebre CADA atividade mencionada em um bloco individual, separado por cômodo/ambiente. Se a fala citar duas atividades em ambientes diferentes, gere dois blocos distintos. Exemplo: "tirei a cerâmica do banheiro social e passei cano na cozinha" →
   [{{"ambiente": "Banheiro Social", "descricao": "Retirada da cerâmica antiga", "concluida": true}},
    {{"ambiente": "Cozinha", "descricao": "Passagem de tubulação hidráulica", "concluida": true}}].
   Use nomes de ambiente padronizados e com iniciais maiúsculas (Banheiro Social, Cozinha, Sala, Quarto, Área de Serviço, Fachada, Muro, Calçada...). Se o ambiente não for citado, use "Geral". concluida=true para o que foi feito hoje; false para o que ficou pendente ou "falta fazer".
3) resumo_executivo: serviços executados no dia em linguagem técnica e profissional para o cliente, coerentes com o escopo. Não invente serviços que não foram relatados nem são visíveis.
4) detalhes_execucao_imprevistos: particularidades técnicas do serviço (complexidade, cuidados, etapas de cura/secagem), dificuldades do terreno ou da estrutura encontrada, clima e justificativas de prazo. Seja factual: NÃO atribua nota, conceito ou avaliação de produtividade ao dia — serviços complexos levam mais tempo sem que isso seja baixa produtividade. Sem particularidades, escreva 'Execução sem particularidades relatadas'.
5) motivo_atraso: chuvas, faltas de funcionários, falta de material ou problemas técnicos que impactaram o prazo, redigidos como justificativa formal (com horários, se citados). Se não houver, escreva exatamente '{SEM_ATRASO}'.
6) mapeamento_asbuilt: o que as fotos mostram — posição e trajeto de tubulações (água fria/quente, esgoto), conduítes, fiação, caixas, ralos, referências de medida (distância do piso, paredes, cantos) e etapa da reforma. Sem foto, use o que o áudio descrever sobre posições; sem nenhuma informação, escreva 'Sem foto para mapeamento As-Built'.
7) AMBIENTE (vale para tarefas e fotos): classifique pelo cômodo citado na fala, nas observações ou no escopo. Palavras como banheiro, suíte, lavabo, cozinha, sala, quarto, área de serviço definem o ambiente. Serviços internos — cerâmica, azulejo, piso, porcelanato, revestimento, rejunte, cano, tubulação, hidráulica, ralo, louças, tomadas, fiação — NUNCA são 'Área Externa': se a fala não disser o cômodo, use o cômodo do contexto da conversa ou do escopo da obra. Ex.: "foi retirado a cerâmica do banheiro" → ambiente "Banheiro" (ou "Banheiro Social"), nunca "Área Externa". Use 'Área Externa' só para quintal, jardim, fossa, vala, muro ou calçada.
8) fotos_classificadas: uma entrada por imagem recebida (indice_foto 0 para "Imagem 0", 1 para "Imagem 1"...), com o ambiente, um titulo_servico curto que cite o serviço e o cômodo (ex.: "Retirada da cerâmica do banheiro") e a descricao_tecnica do que aquela imagem mostra. Sem fotos: lista vazia.
9) foto_indices (VÍNCULO VISUAL imagem ↔ tarefa): cada imagem chega rotulada como "Imagem 0", "Imagem 1"... Em cada item de tarefas_extraidas, preencha foto_indices APENAS com os índices das imagens cujo CONTEÚDO VISUAL mostra aquela tarefa específica (ex.: cerâmica sendo retirada → tarefa de retirada da cerâmica; vala aberta → tarefa de escavação).
   - Estar no mesmo cômodo NÃO basta: com 10 imagens de coisas diferentes, cada tarefa recebe só as imagens que batem com ela.
   - Se uma imagem mostra duas tarefas juntas (ex.: vala escavada com os anéis já instalados), o índice pode aparecer nas duas tarefas ou só na principal.
   - Imagem que não mostra nenhuma tarefa específica (visão geral, entulho, material parado, foto sem relação clara) não entra em nenhum foto_indices.
   - Use somente índices existentes (0 a N-1). Sem imagens: foto_indices vazio em todas as tarefas.
"""


def _montar_prompt(contexto_obra: str, observacoes_texto: str, tem_audio: bool, qtd_fotos: int,
                   transcricao_literal: str = "") -> str:
    """Monta o texto final do pedido ao Gemini, enviado depois do áudio e das imagens.

    Args:
        contexto_obra: Cliente, endereço, escopo e status da obra.
        observacoes_texto: Observações digitadas no dia.
        tem_audio: Se há áudio no pedido.
        qtd_fotos: Quantidade de imagens enviadas. Elas são nomeadas "Imagem 0" a
            "Imagem N-1", na mesma ordem usada em `foto_indices`.
        transcricao_literal: Transcrição pt-BR já obtida; vira a base da `transcricao_audio`.

    Returns:
        Prompt em português com o contexto, as fontes recebidas e o lembrete do vínculo visual.
    """
    fontes = []
    if tem_audio:
        fontes.append("um ÁUDIO com o relato do trabalhador (fonte principal)")
    if qtd_fotos == 1:
        fontes.append("1 IMAGEM do serviço executado (Imagem 0)")
    elif qtd_fotos > 1:
        fontes.append(f"{qtd_fotos} IMAGENS do serviço executado (Imagem 0 a Imagem {qtd_fotos - 1}, na ordem enviada)")
    if observacoes_texto:
        fontes.append("OBSERVAÇÕES escritas")
    fontes_txt = ", ".join(fontes) if fontes else "nenhuma mídia (apenas o contexto)"
    literal = (
        f"TRANSCRIÇÃO LITERAL DO ÁUDIO (reconhecimento de fala pt-BR, sem pontuação):\n\"{transcricao_literal}\"\n\n"
        if transcricao_literal else ""
    )
    return (
        f"CONTEXTO DA OBRA:\n{contexto_obra or 'Não informado.'}\n\n"
        f"{literal}"
        f"OBSERVAÇÕES ESCRITAS DO DIA:\n{observacoes_texto or 'Nenhuma.'}\n\n"
        f"Você recebeu: {fontes_txt}. Analise seguindo as regras do sistema."
        + (" Em cada tarefa, preencha foto_indices só com as imagens que mostram aquela tarefa." if qtd_fotos else "")
    )


def _parte_audio(client: genai.Client, caminho_audio: Path) -> tuple[types.Part | types.File, Optional[types.File]]:
    """Prepara o áudio para o `contents` do Gemini.

    Até `LIMITE_AUDIO_INLINE_BYTES` o áudio vai inline; acima disso vai pela Files API, com
    espera de até 120 s pelo processamento remoto.

    Args:
        client: Cliente `genai` já autenticado.
        caminho_audio: Arquivo de áudio local.

    Returns:
        Tupla (parte para o `contents`, arquivo remoto ou None). O arquivo remoto deve ser
        apagado pelo chamador ao final (ver `processar_registro_obra`).

    Raises:
        TimeoutError: O processamento remoto passou de 120 s.
        RuntimeError: A Files API marcou o arquivo como FAILED.
    """
    mime = MIME_AUDIO.get(caminho_audio.suffix.lower(), "audio/wav")
    audio_bytes = caminho_audio.read_bytes()

    if len(audio_bytes) <= LIMITE_AUDIO_INLINE_BYTES:
        return types.Part.from_bytes(data=audio_bytes, mime_type=mime), None

    # Relatos muito longos (> ~8 min em WAV 16 kHz) excedem o limite inline.
    arquivo = client.files.upload(file=caminho_audio, config=types.UploadFileConfig(mime_type=mime))
    prazo = time.monotonic() + 120
    while arquivo.state == types.FileState.PROCESSING:
        if time.monotonic() > prazo:
            raise TimeoutError("Tempo esgotado aguardando o processamento do áudio.")
        time.sleep(2)
        arquivo = client.files.get(name=arquivo.name)
    if arquivo.state == types.FileState.FAILED:
        raise RuntimeError("Falha no processamento remoto do arquivo de áudio.")
    return arquivo, arquivo


def _carregar_foto(caminho_foto: Path) -> Image.Image:
    """Abre uma foto para envio ao Gemini, com a rotação do EXIF aplicada.

    Args:
        caminho_foto: Arquivo de imagem local.

    Returns:
        Imagem RGB decodificada.
    """
    with Image.open(caminho_foto) as img:
        img.load()
        return ImageOps.exif_transpose(img).convert("RGB")


def _interpretar_resposta(response: types.GenerateContentResponse) -> AnaliseDiarioObra:
    """Converte a resposta do Gemini no schema `AnaliseDiarioObra`.

    Args:
        response: Resposta de `generate_content` com `response_schema` configurado.

    Returns:
        A análise validada pelo Pydantic.

    Raises:
        ValueError: Resposta vazia (ex.: bloqueio de segurança do modelo).
        pydantic.ValidationError: JSON fora do schema.
    """
    if isinstance(response.parsed, AnaliseDiarioObra):
        return response.parsed
    if not response.text:
        raise ValueError("Resposta vazia do modelo (possível bloqueio de segurança).")
    return AnaliseDiarioObra.model_validate_json(response.text)


def _gerar_com_fallback(client: genai.Client, contents: list) -> AnaliseDiarioObra:
    """Chama a cadeia de modelos do Gemini até obter uma análise válida.

    Regras: 429/503 repetem o mesmo modelo (`TENTATIVAS_POR_MODELO`, com pausa de
    `ESPERA_RETRY_SEGUNDOS`); 404, 500, 504, timeout e rede passam direto ao próximo modelo;
    resposta fora do schema conta como tentativa falha.

    Args:
        client: Cliente `genai` com o retry interno do SDK desligado.
        contents: Áudio, imagens rotuladas ("Imagem 0:", ...) e prompt.

    Returns:
        A análise do primeiro modelo que responder corretamente, com `_modelo` preenchido.

    Raises:
        errors.APIError: Chave inválida/sem permissão (trocar de modelo não resolve).
        RuntimeError: Todos os modelos falharam; a mensagem lista cada falha.
    """
    config = types.GenerateContentConfig(
        system_instruction=INSTRUCAO_SISTEMA,
        response_mime_type="application/json",
        response_schema=AnaliseDiarioObra,
        temperature=0.2,
    )
    falhas: list[str] = []
    modelos = _modelos()
    for modelo in modelos:
        for tentativa in range(1, TENTATIVAS_POR_MODELO + 1):
            try:
                response = client.models.generate_content(model=modelo, contents=contents, config=config)
                analise = _interpretar_resposta(response)
                analise._modelo = modelo
                if falhas:
                    log.warning("Análise concluída com %s após falhas: %s", modelo, " | ".join(falhas))
                return analise
            except errors.APIError as exc:
                if _erro_de_chave(exc):
                    raise
                falhas.append(f"{modelo}#{tentativa}: {exc.code} {exc.status}")
                if exc.code not in CODIGOS_RETRY_MESMO_MODELO:
                    break  # 404, 504, 500 etc.: próximo modelo (sem repetir este)
            except (ValidationError, ValueError) as exc:
                falhas.append(f"{modelo}#{tentativa}: resposta inválida ({type(exc).__name__})")
            except Exception as exc:  # timeout/rede do cliente: próximo modelo
                falhas.append(f"{modelo}#{tentativa}: {type(exc).__name__}")
                break
            if tentativa < TENTATIVAS_POR_MODELO:
                time.sleep(ESPERA_RETRY_SEGUNDOS)
        # Pausa antes do próximo modelo — exceto se este simplesmente não existe (404).
        if modelo != modelos[-1] and not falhas[-1].endswith("NOT_FOUND"):
            time.sleep(ESPERA_RETRY_SEGUNDOS)
    raise RuntimeError("Todos os modelos falharam: " + " | ".join(falhas))


# --------------------------------------------------------------------------- #
# Transcrição dedicada (Speech-to-Text pt-BR), independente da IA
# --------------------------------------------------------------------------- #
def _ffmpeg() -> Optional[str]:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        log.error("ffmpeg indisponível para converter o áudio (%s).", exc)
        return None


def _carregar_audio_pcm(caminho: Path):
    """Qualquer áudio gravado no navegador/celular → pydub.AudioSegment PCM 16 kHz mono 16 bits."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # pydub avisa que não achou ffmpeg no PATH (usamos o do imageio)
        from pydub import AudioSegment
    exe = _ffmpeg()
    if exe:
        AudioSegment.converter = exe
    if caminho.suffix.lower() == ".wav":
        try:
            audio = AudioSegment.from_wav(str(caminho))  # leitura nativa, sem ffmpeg
            return audio.set_frame_rate(TAXA_STT_HZ).set_channels(1).set_sample_width(2)
        except Exception:
            pass  # WAV não-PCM (ex.: float/ADPCM): converte com ffmpeg abaixo
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        saida = Path(tmp) / "audio_pcm.wav"
        proc = subprocess.run(
            [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(caminho),
             "-ac", "1", "-ar", str(TAXA_STT_HZ), "-sample_fmt", "s16", "-f", "wav", str(saida)],
            capture_output=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not saida.is_file():
            log.error("Conversão do áudio falhou: %s", proc.stderr.decode("utf-8", "replace")[:300])
            return None
        return AudioSegment.from_wav(str(saida))


def _trechos_de_fala(audio) -> list:
    """Divide em trechos de até 35 s, cortando no silêncio mais próximo do fim de cada trecho."""
    from pydub.silence import detect_silence

    limiar = (audio.dBFS - 16) if audio.dBFS != float("-inf") else -50
    trechos, inicio, total = [], 0, len(audio)
    while total - inicio > 300:
        fim = min(inicio + TRECHO_STT_MS, total)
        if fim < total:
            janela = audio[fim - JANELA_CORTE_MS:fim]
            silencios = detect_silence(janela, min_silence_len=SILENCIO_MIN_MS, silence_thresh=limiar, seek_step=20)
            if silencios:
                ini_s, fim_s = silencios[-1]
                fim = fim - JANELA_CORTE_MS + (ini_s + fim_s) // 2
        trechos.append(audio[inicio:fim])
        inicio = fim
    return trechos


def transcrever_audio_garantido(caminho_audio: Optional[str | Path]) -> str:
    """Transcreve, em português, tudo o que foi falado no áudio (inclusive mais de 1 min).

    Converte para WAV PCM 16 kHz mono, divide em trechos de até 35 s cortando nos silêncios e
    transcreve cada um com `recognize_google(..., language="pt-BR")`. Trechos inaudíveis são
    pulados.

    Args:
        caminho_audio: Arquivo de áudio (WAV, MP3, OGG, WEBM...) ou None.

    Returns:
        Texto reconhecido, ou "" se não houver fala, áudio ou serviço disponível.

    Note:
        Regra de negócio: nunca levanta exceção. A transcrição independe da IA, e o registro
        do dia sempre pode ser salvo.
    """
    if not caminho_audio:
        return ""
    caminho = Path(caminho_audio)
    if not caminho.is_file():
        log.warning("Transcrição dedicada ignorada: arquivo de áudio ausente.")
        return ""
    try:
        import speech_recognition as sr
    except ImportError:
        log.error("Pacote SpeechRecognition não instalado; transcrição dedicada indisponível.")
        return ""

    try:
        audio = _carregar_audio_pcm(caminho)
    except Exception as exc:
        log.error("Falha ao preparar o áudio para transcrição (%s: %s).", type(exc).__name__, exc)
        audio = None
    if audio is None or len(audio) < 300:
        return ""

    reconhecedor = sr.Recognizer()
    reconhecedor.operation_timeout = TIMEOUT_STT_SEGUNDOS
    partes: list[str] = []
    trechos = _trechos_de_fala(audio)
    for i, trecho in enumerate(trechos, start=1):
        buf = io.BytesIO()
        trecho.export(buf, format="wav")
        buf.seek(0)
        try:
            with sr.AudioFile(buf) as fonte:
                dados = reconhecedor.record(fonte)
            texto = reconhecedor.recognize_google(dados, language="pt-BR")
        except sr.UnknownValueError:
            continue  # trecho sem fala reconhecível
        except sr.RequestError as exc:
            log.error("Serviço de transcrição indisponível no trecho %d/%d (%s).", i, len(trechos), exc)
            continue
        except Exception as exc:  # timeout etc.: segue para o próximo trecho
            log.error("Falha na transcrição do trecho %d/%d (%s: %s).", i, len(trechos), type(exc).__name__, exc)
            continue
        if isinstance(texto, str) and texto.strip():
            partes.append(texto.strip())
    log.info("Transcrição dedicada: %d de %d trecho(s) reconhecido(s), %.1f s de áudio.",
             len(partes), len(trechos), len(audio) / 1000)
    texto = " ".join(partes).strip()
    return texto[:1].upper() + texto[1:] if texto else ""


# --------------------------------------------------------------------------- #
# Análise offline (regras locais) — usada só se TODA a cadeia de IA falhar
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
_REGRAS_DETALHES = [
    (r"\bpedra|rocha|rochos|terreno\s+duro|solo\s+duro|rompedor", "Terreno com rocha/pedra, exigindo escavação mais lenta"),
    (r"len[çc]ol|minando\s+[aá]gua|encharc|lama|barro", "Solo úmido/encharcado (água no terreno) durante a execução"),
    (r"\bcura\b|secagem|secar|esperar\s+(?:o\s+)?(?:concreto|reboco|massa|argamassa)", "Etapa depende de tempo de cura/secagem do material"),
    (r"viga|pilar|ferragem|ferro\s+expost|estrutura|laje", "Interferência com a estrutura (viga/pilar/laje) exigiu cuidado adicional"),
    (r"tubula[çc][aã]o\s+antiga|cano\s+antigo|fia[çc][aã]o\s+antiga|instala[çc][aã]o\s+antiga", "Instalações antigas encontradas na abertura, exigindo adequação"),
]
_SINAIS_PARALISACAO = r"paramos|parou|paralis|suspens|n[aã]o\s+(?:trabalhamos|deu\s+para|foi\s+poss[ií]vel)|sem\s+trabalho"
_SINAIS_PENDENTE = r"\b(?:falta|faltou|precisa|amanh[aã]|vamos|vai\s+ser|pendente|ainda\s+n[aã]o|depois)\b"
# Fala transcrita vem sem pontuação: além de vírgula/ponto, quebra antes de conectivos
# ("depois", "amanhã", "também"...) e de "e"/"eu"/"a gente" seguidos de verbo no passado.
_VERBO_FALA = r"\w+(?:ei|amos|ou|emos|imos|i|aram|eram|iram)\b"
_COMODO_FALA = (r"(?:banheiro|lavabo|su[ií]te|cozinha|sala|quarto|varanda|garagem|lavanderia|"
                r"[aá]rea\s+(?:externa|de\s+servi[çc]o)|quintal|jardim|fachada|muro|cal[çc]ada|telhado|laje)")
_SEPARADORES_FALA = (
    r"[;,.!?]|\s+e\s+(?=(?:(?:eu|a\s+gente|n[oó]s|o\s+\w+)\s+)?" + _VERBO_FALA + ")"
    r"|\s+(?=(?:depois|ent[aã]o|a[ií]|tamb[eé]m|amanh[aã]|em\s+seguida|al[eé]m\s+disso|agora)\b)"
    # troca de assunto/cômodo: "... de hoje | no banheiro nós retiramos ..."
    r"|\s+(?=(?:n[oa]s?|sobre\s+[oa])\s+" + _COMODO_FALA + r"\s+(?:(?:eu|n[oó]s|a\s+gente)\s+)?" + _VERBO_FALA + ")"
)
# Saudações e despedidas do relato falado (não são serviço; "até amanhã" não é pendência).
_FALAS_SOCIAIS = (
    r"\b(?:bom\s+dia|boa\s+tarde|boa\s+noite|ol[aá]|oi)\b"
    r"|\baqui\s+(?:[eé]|est[aá])\s+(?:o\s+)?(?:relato|resumo|[aá]udio)(?:\s+d[ao]\s+(?:obra|dia))?(?:\s+de\s+hoje)?"
    r"|\b(?:era|[eé]|foi)\s+isso(?:\s+a[ií])?\b|\bat[eé]\s+(?:amanh[aã]|mais|logo|segunda)\b"
    r"|\b(?:tchau|valeu|obrigad[oa])\b"
)
_PREFIXOS_FALA = (r"^(?:e|ent[aã]o|a[ií]|depois|tamb[eé]m|em\s+seguida|al[eé]m\s+disso|agora|hoje|eu|a\s+gente|"
                  r"n[oó]s|n[eé]|sobre\s+[oa])\b\s*")
LIMITE_DESCRICAO_TAREFA = 200
LIMITE_TITULO_TAREFA = 150
# "vala/poço de infiltração" é serviço (sistema de esgoto), não infiltração de água (problema).
_SERVICO_INFILTRACAO = r"(?:valas?|po[çc]os?|sistema|campo|caixa)\s+de\s+infiltra\w*"
# Intenção futura no início da tarefa pendente ("amanhã vamos assentar..." → "Assentar...").
_PREFIXO_PENDENTE = r"^(?:amanh[aã]\s+)?(?:a\s+gente\s+|n[oó]s\s+|eu\s+)?(?:vamos|vai|vou|precisa(?:mos)?|falta)\s+(?:a\s+gente\s+)?"


def _texto_para_atraso(texto: str) -> str:
    return re.sub(_SERVICO_INFILTRACAO, " ", texto, flags=re.IGNORECASE)


def _segmentos_fala(obs: str) -> list[tuple[str, bool]]:
    """Trechos de atividade do relato: [(texto, concluida)], sem atrasos/paralisações."""
    segmentos = []
    obs = re.sub(_FALAS_SOCIAIS, " ", obs, flags=re.IGNORECASE)
    obs = re.sub(r"\s{2,}", " ", obs)
    for parte in (p.strip() for p in re.split(_SEPARADORES_FALA, obs, flags=re.IGNORECASE) if p):
        # Vícios de fala no início do trecho ("e aí eu tirei..." → "tirei...").
        while (limpo := re.sub(_PREFIXOS_FALA, "", parte, count=1, flags=re.IGNORECASE).strip()) != parte:
            parte = limpo
        baixa = _texto_para_atraso(parte.lower())
        if (
            len(parte) < 6
            or any(re.search(padrao, baixa) for padrao, _ in _REGRAS_ATRASO)
            or re.search(_SINAIS_PARALISACAO, baixa)
        ):
            continue  # fragmentos e frases de atraso/paralisação não são atividade
        concluida = not re.search(_SINAIS_PENDENTE, parte.lower())
        if not concluida:
            parte = re.sub(_PREFIXO_PENDENTE, "", parte, flags=re.IGNORECASE).strip() or parte
        if len(parte) >= 6:
            segmentos.append((parte[0].upper() + parte[1:], concluida))
    return segmentos


def _frase(texto: str) -> str:
    texto = texto.strip()
    return texto if texto.endswith((".", "!", "?")) else texto + "."


def _detalhes_de(texto: str) -> str:
    """Particularidades técnicas e imprevistos citados num trecho do relato."""
    alvo = _texto_para_atraso(texto.lower())
    achados = [d for padrao, d in _REGRAS_DETALHES if re.search(padrao, alvo)]
    achados += [d for padrao, d in _REGRAS_ATRASO if re.search(padrao, alvo)]
    return ("; ".join(dict.fromkeys(achados)) + ".") if achados else SEM_PARTICULARIDADES


def _tarefas_por_regras(obs: str, contexto_obra: str = "") -> list[ItemTarefa]:
    """Separa o relato em serviços por ambiente, cada um com resumo e detalhes exclusivos.

    1) quebra a fala em trechos (vírgula, ponto, conectivos como "depois"/"amanhã");
    2) ambiente de cada trecho: o cômodo citado nele; sem cômodo, o do trecho anterior
       (continuação do mesmo assunto: "tirei a cerâmica do banheiro e troquei o registro") —
       exceto serviço interno logo após um ambiente externo; por fim, o relato/escopo;
    3) agrupa os trechos do mesmo ambiente e status (feito/pendente) numa só tarefa:
       banheiro/cerâmica/encanamento numa, vala/fossa/anéis/sumidouro em outra.
    """
    blocos: dict[tuple[str, bool], dict] = {}
    anterior: Optional[str] = None
    for parte, concluida in _segmentos_fala(obs):
        if ambientes_citados(parte) or anterior is None or (
            anterior in AMBIENTES_EXTERNOS and eh_servico_interno(parte)
        ):
            ambiente = corrigir_ambiente("Geral", parte, obs, contexto_obra)
        else:
            ambiente = anterior
        anterior = ambiente
        bloco = blocos.setdefault((normalizar_busca(ambiente), concluida),
                                  {"ambiente": ambiente, "concluida": concluida, "partes": []})
        bloco["partes"].append(parte)

    tarefas = []
    for b in blocos.values():
        titulo = "; ".join(b["partes"])
        if len(titulo) > LIMITE_TITULO_TAREFA:
            titulo = titulo[: LIMITE_TITULO_TAREFA - 1].rsplit(" ", 1)[0] + "…"
        texto = " ".join(_frase(p) for p in b["partes"])
        tarefas.append(
            ItemTarefa(
                ambiente=b["ambiente"],
                descricao=titulo,
                resumo_servico=texto[:LIMITE_DESCRICAO_TAREFA * 5],
                detalhes_servico=_detalhes_de(texto),
                concluida=b["concluida"],
            )
        )
    return tarefas


def _lista_fotos(fotos: Optional[str | Path | list]) -> list[Path]:
    if not fotos:
        return []
    if isinstance(fotos, (str, Path)):
        return [Path(fotos)]
    return [Path(f) for f in fotos if f]


def gerar_analise_offline(
    observacoes_texto: str = "",
    contexto_obra: str = "",
    tem_audio: bool = False,
    caminho_foto: Optional[Path | list[Path]] = None,
    transcricao: str = "",
) -> AnaliseDiarioObra:
    """Gera a análise do dia por regras locais, sem chamar a IA.

    A transcrição e as observações formam o relato, de onde saem o resumo, os
    detalhes/imprevistos, o atraso e as tarefas separadas por ambiente.

    Args:
        observacoes_texto: Observações digitadas no dia.
        contexto_obra: Contexto da obra (escopo ajuda a deduzir o cômodo).
        tem_audio: Se havia áudio no registro.
        caminho_foto: Uma foto, uma lista de fotos ou None.
        transcricao: Texto real falado no áudio, se disponível.

    Returns:
        `AnaliseDiarioObra` marcada como offline (`modo_offline` True, com `aviso`).

    Note:
        Sem visão computacional, as fotos não são classificadas e nenhuma tarefa recebe
        `foto_indices`; quem salva decide o vínculo (ver `vincular_fotos_do_registro`).
    """
    fotos = _lista_fotos(caminho_foto)
    transcricao = (transcricao or "").strip()
    obs = ". ".join(t for t in (transcricao, (observacoes_texto or "").strip()) if t)
    texto = _texto_para_atraso(obs.lower())

    causas = [descricao for padrao, descricao in _REGRAS_ATRASO if re.search(padrao, texto)]
    paralisou = bool(re.search(_SINAIS_PARALISACAO, texto))

    if causas:
        motivo = "; ".join(causas) + "."
        horarios = re.findall(r"\b\d{1,2}\s*(?:h|:\d{2})\s*(?:\d{2})?\b", obs)
        if horarios:
            motivo += f" Horários citados no relato: {', '.join(h.strip() for h in horarios)}."
    else:
        motivo = SEM_ATRASO

    detalhes = [d for padrao, d in _REGRAS_DETALHES if re.search(padrao, texto)] + causas
    if paralisou:
        detalhes.append("Houve paralisação dos serviços durante o dia")
    detalhes_txt = ("; ".join(detalhes) + ".") if detalhes else SEM_PARTICULARIDADES

    escopo = next(
        (linha.split(":", 1)[1].strip() for linha in contexto_obra.splitlines() if linha.lower().startswith("escopo")),
        "",
    )
    tarefas = _tarefas_por_regras(obs, contexto_obra)
    feitas = [f"{t.ambiente}: {t.resumo_servico.rstrip('.')}" for t in tarefas if t.concluida]
    pendentes = [f"{t.ambiente}: {t.resumo_servico.rstrip('.')}" for t in tarefas if not t.concluida]
    if feitas or pendentes:
        resumo = ""
        if feitas:
            resumo += "Serviços executados no dia — " + " | ".join(feitas) + "."
        if pendentes:
            resumo += (" " if resumo else "") + "Previstos/pendentes — " + " | ".join(pendentes) + "."
    elif obs:
        resumo = f"Relato do dia: {obs[0].upper()}{obs[1:]}"
        resumo += "" if resumo.endswith(".") else "."
    elif tem_audio:
        resumo = ("Relato do dia gravado em áudio. O resumo será preenchido quando a fala for "
                  "transcrita (botão 🔄 Transcrever o áudio agora, no Histórico).")
    else:
        resumo = "Registro fotográfico do dia, sem relato falado ou escrito."
    if escopo and escopo.lower() != "não informado":
        resumo += f" Serviços enquadrados no escopo contratado: {escopo}"

    if fotos:
        qtd = f"{len(fotos)} fotos do dia arquivadas" if len(fotos) > 1 else "Foto do dia arquivada"
        asbuilt = (
            f"{qtd} como evidência As-Built. A descrição da posição de tubulações, conduítes e "
            "fiação pode ser completada na Galeria da Obra ou editando este registro."
        )
    else:
        asbuilt = SEM_FOTO_ASBUILT

    analise = AnaliseDiarioObra(
        transcricao_audio=transcricao or (TRANSCRICAO_INDISPONIVEL if tem_audio else ""),
        resumo_executivo=resumo,
        detalhes_execucao_imprevistos=detalhes_txt,
        motivo_atraso=motivo,
        mapeamento_asbuilt=asbuilt,
        tarefas_extraidas=tarefas,
    )
    analise._modo_offline = True
    analise._aviso = AVISO_ANALISE_SIMPLIFICADA
    return analise


# --------------------------------------------------------------------------- #
# Função principal
# --------------------------------------------------------------------------- #
def _corrigir_ambientes(analise: AnaliseDiarioObra, relato: str, contexto_obra: str, qtd_fotos: int) -> AnaliseDiarioObra:
    """Aplica as regras locais sobre a saída da IA e descarta índices inválidos.

    - Ambiente: palavras-chave prevalecem (ex.: cerâmica do banheiro classificada como
      'Área Externa' volta para 'Banheiro').
    - `foto_indices`: mantém só índices existentes (0 a `qtd_fotos` - 1), sem repetição e
      ordenados.
    - `fotos_classificadas`: mantém uma entrada por índice existente.

    Args:
        analise: Análise devolvida pelo modelo (alterada no lugar).
        relato: Transcrição + observações do dia.
        contexto_obra: Contexto/escopo da obra.
        qtd_fotos: Quantidade de imagens realmente enviadas.

    Returns:
        A mesma `analise`, corrigida.
    """
    for t in analise.tarefas_extraidas:
        t.ambiente = corrigir_ambiente(t.ambiente, t.descricao, relato, contexto_obra)
        # Só índices existentes (0..N-1), sem repetição, em ordem.
        t.foto_indices = sorted({i for i in t.foto_indices if 0 <= i < qtd_fotos})
    vistas: set[int] = set()
    fotos = []
    for f in analise.fotos_classificadas:
        if 0 <= f.indice_foto < qtd_fotos and f.indice_foto not in vistas:
            vistas.add(f.indice_foto)
            f.ambiente = corrigir_ambiente(f.ambiente, f"{f.titulo_servico} {f.descricao_tecnica}", relato, contexto_obra)
            fotos.append(f)
    analise.fotos_classificadas = fotos
    return analise


def processar_registro_obra(
    caminho_audio: Optional[str | Path] = None,
    caminho_foto: Optional[str | Path | list] = None,
    observacoes_texto: str = "",
    contexto_obra: str = "",
    modo_offline: Optional[bool] = None,
    transcricao_pronta: Optional[str] = None,
) -> AnaliseDiarioObra:
    """Analisa áudio, fotos e observações de um dia de obra (IA multimodal).

    Fluxo:
        1. transcrição pt-BR dedicada do áudio (sempre, mesmo sem IA);
        2. envio ao Gemini do áudio e de cada foto precedida do rótulo "Imagem i:" (base 0);
        3. o modelo devolve as tarefas por ambiente, cada uma com `foto_indices`: só as
           imagens que mostram visualmente aquela tarefa;
        4. `_corrigir_ambientes` valida ambientes e índices.

    Args:
        caminho_audio: Áudio do relato ou None.
        caminho_foto: Uma foto, uma lista de fotos ou None. A ordem define os índices.
        observacoes_texto: Observações digitadas.
        contexto_obra: Contexto da obra para o modelo.
        modo_offline: True força a análise local; None usa a configuração do servidor.
        transcricao_pronta: Transcrição já feita (reprocessamento), para não transcrever de novo.

    Returns:
        `AnaliseDiarioObra` validada. Se a IA estiver indisponível, sem chave ou falhar em
        todos os modelos, devolve a análise offline, com `modo_offline` True.

    Raises:
        FileNotFoundError: Algum arquivo de áudio/foto informado não existe.
        ValueError: Nenhum áudio, foto ou observação foi enviado.

    Note:
        Regra de negócio: indisponibilidade da IA nunca impede salvar o registro.
        Segurança: a chave vem só do servidor, e o áudio enviado pela Files API é apagado
        ao final (best-effort; arquivos remotos expiram em 48 h).
    """
    audio_path = Path(caminho_audio) if caminho_audio else None
    fotos = _lista_fotos(caminho_foto)
    observacoes_texto = (observacoes_texto or "").strip()

    for p in (audio_path, *fotos):
        if p is not None and not p.is_file():
            raise FileNotFoundError(f"Arquivo não encontrado: {p}")
    if not (audio_path or fotos or observacoes_texto):
        raise ValueError("Envie pelo menos um áudio, uma foto ou observações em texto.")

    # 1º passo, sempre: o texto real falado no áudio (independe da IA estar disponível).
    if transcricao_pronta is not None:
        transcricao_literal = transcricao_pronta.strip()  # já transcrito (ex.: reprocessamento)
    else:
        transcricao_literal = transcrever_audio_garantido(audio_path) if audio_path else ""

    def offline() -> AnaliseDiarioObra:
        return gerar_analise_offline(observacoes_texto, contexto_obra, audio_path is not None, fotos,
                                     transcricao=transcricao_literal)

    if modo_offline if modo_offline is not None else modo_offline_forcado():
        return offline()

    chave = _obter_api_key()
    if not chave:
        log.error("GEMINI_API_KEY ausente no ambiente do servidor; usando análise offline.")
        return offline()

    client = genai.Client(
        api_key=chave,
        # Retry é controlado aqui (cadeia de modelos); o retry interno do SDK é desligado
        # para que um 503 não prenda a requisição por minutos no mesmo modelo.
        http_options=types.HttpOptions(
            timeout=TIMEOUT_REQUISICAO_MS,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    arquivo_remoto: Optional[types.File] = None
    try:
        contents: list = []
        if audio_path:
            parte_audio, arquivo_remoto = _parte_audio(client, audio_path)
            contents.append(parte_audio)
        for i, foto in enumerate(fotos):  # índice explícito antes de cada imagem (base 0)
            contents += [f"Imagem {i}:", _carregar_foto(foto)]
        contents.append(
            _montar_prompt(contexto_obra, observacoes_texto, tem_audio=audio_path is not None, qtd_fotos=len(fotos),
                           transcricao_literal=transcricao_literal)
        )
        analise = _gerar_com_fallback(client, contents)
        if transcricao_literal:
            # O campo guarda o que foi falado de fato (reconhecimento de fala dedicado).
            analise.transcricao_audio = transcricao_literal
        elif audio_path and not analise.transcricao_audio.strip():
            analise.transcricao_audio = TRANSCRICAO_INDISPONIVEL
        if fotos and SEM_FOTO_ASBUILT.lower() in analise.mapeamento_asbuilt.lower():
            analise.mapeamento_asbuilt = ASBUILT_FOTO_SEM_DESCRICAO
        relato = f"{analise.transcricao_audio} {observacoes_texto}"
        analise = _corrigir_ambientes(analise, relato, contexto_obra, len(fotos))
        log.info("Registro analisado com %s.", analise.modelo)
        return analise
    except errors.APIError as exc:
        if _erro_de_chave(exc):
            log.error("Chave da API rejeitada pelo Google (%s %s); usando análise offline.", exc.code, exc.status)
        else:
            log.error("Falha na API de IA (%s %s); usando análise offline.", exc.code, exc.status)
        return offline()
    except Exception as exc:
        log.error("Falha na análise de IA (%s); usando análise offline.", exc)
        return offline()
    finally:
        if arquivo_remoto is not None:
            try:
                client.files.delete(name=arquivo_remoto.name)
            except Exception:
                pass  # limpeza best-effort; arquivos remotos expiram em 48h
