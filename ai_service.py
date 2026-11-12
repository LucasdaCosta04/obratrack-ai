"""Motor de IA multimodal do ObraTrack AI (Gemini) com cadeia de modelos e análise offline.

Segurança: a chave da API é lida EXCLUSIVAMENTE do ambiente do servidor (variável de
ambiente ou arquivo .env). Ela nunca é recebida pela interface, e nenhuma mensagem
destinada ao usuário final menciona chave, variáveis de ambiente ou configurações.
Detalhes técnicos de falhas vão apenas para o log do servidor.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, PrivateAttr, ValidationError

from database import corrigir_ambiente

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
    descricao: str = Field(description="Atividade em uma frase curta e técnica (ex.: 'Retirada da cerâmica antiga').")
    concluida: bool = Field(
        description="True se o relato diz que foi feito hoje; False se diz que ainda falta fazer ou ficou pendente."
    )


class ItemFoto(BaseModel):
    """Classificação de uma foto enviada no registro (na ordem de envio)."""

    indice_foto: int = Field(description="Número da foto na ordem enviada, começando em 1 (FOTO 1, FOTO 2...).")
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
    """Lê a chave estritamente do ambiente do servidor (env var tem prioridade sobre o .env)."""
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
1) transcricao_audio: transcrição LITERAL e COMPLETA de tudo o que foi falado no áudio, do início ao fim, sem resumir, sem omitir trechos e sem corrigir o sentido (mantenha gírias e termos de obra). Sem áudio: string vazia.
2) tarefas_extraidas: quebre CADA atividade mencionada em um bloco individual, separado por cômodo/ambiente. Se a fala citar duas atividades em ambientes diferentes, gere dois blocos distintos. Exemplo: "tirei a cerâmica do banheiro social e passei cano na cozinha" →
   [{{"ambiente": "Banheiro Social", "descricao": "Retirada da cerâmica antiga", "concluida": true}},
    {{"ambiente": "Cozinha", "descricao": "Passagem de tubulação hidráulica", "concluida": true}}].
   Use nomes de ambiente padronizados e com iniciais maiúsculas (Banheiro Social, Cozinha, Sala, Quarto, Área de Serviço, Fachada, Muro, Calçada...). Se o ambiente não for citado, use "Geral". concluida=true para o que foi feito hoje; false para o que ficou pendente ou "falta fazer".
3) resumo_executivo: serviços executados no dia em linguagem técnica e profissional para o cliente, coerentes com o escopo. Não invente serviços que não foram relatados nem são visíveis.
4) detalhes_execucao_imprevistos: particularidades técnicas do serviço (complexidade, cuidados, etapas de cura/secagem), dificuldades do terreno ou da estrutura encontrada, clima e justificativas de prazo. Seja factual: NÃO atribua nota, conceito ou avaliação de produtividade ao dia — serviços complexos levam mais tempo sem que isso seja baixa produtividade. Sem particularidades, escreva 'Execução sem particularidades relatadas'.
5) motivo_atraso: chuvas, faltas de funcionários, falta de material ou problemas técnicos que impactaram o prazo, redigidos como justificativa formal (com horários, se citados). Se não houver, escreva exatamente '{SEM_ATRASO}'.
6) mapeamento_asbuilt: o que as fotos mostram — posição e trajeto de tubulações (água fria/quente, esgoto), conduítes, fiação, caixas, ralos, referências de medida (distância do piso, paredes, cantos) e etapa da reforma. Sem foto, use o que o áudio descrever sobre posições; sem nenhuma informação, escreva 'Sem foto para mapeamento As-Built'.
7) AMBIENTE (vale para tarefas e fotos): classifique pelo cômodo citado na fala, nas observações ou no escopo. Palavras como banheiro, suíte, lavabo, cozinha, sala, quarto, área de serviço definem o ambiente. Serviços internos — cerâmica, azulejo, piso, porcelanato, revestimento, rejunte, cano, tubulação, hidráulica, ralo, louças, tomadas, fiação — NUNCA são 'Área Externa': se a fala não disser o cômodo, use o cômodo do contexto da conversa ou do escopo da obra. Ex.: "foi retirado a cerâmica do banheiro" → ambiente "Banheiro" (ou "Banheiro Social"), nunca "Área Externa". Use 'Área Externa' só para quintal, jardim, fossa, vala, muro ou calçada.
8) fotos_classificadas: uma entrada por foto recebida (FOTO 1, FOTO 2...), com o ambiente, um titulo_servico curto que cite o serviço e o cômodo (ex.: "Retirada da cerâmica do banheiro") e a descricao_tecnica do que aquela foto mostra. Sem fotos: lista vazia.
"""


def _montar_prompt(contexto_obra: str, observacoes_texto: str, tem_audio: bool, qtd_fotos: int) -> str:
    fontes = []
    if tem_audio:
        fontes.append("um ÁUDIO com o relato do trabalhador (fonte principal)")
    if qtd_fotos == 1:
        fontes.append("1 FOTO do serviço executado (FOTO 1)")
    elif qtd_fotos > 1:
        fontes.append(f"{qtd_fotos} FOTOS do serviço executado (FOTO 1 a FOTO {qtd_fotos}, na ordem enviada)")
    if observacoes_texto:
        fontes.append("OBSERVAÇÕES escritas")
    fontes_txt = ", ".join(fontes) if fontes else "nenhuma mídia (apenas o contexto)"
    return (
        f"CONTEXTO DA OBRA:\n{contexto_obra or 'Não informado.'}\n\n"
        f"OBSERVAÇÕES ESCRITAS DO DIA:\n{observacoes_texto or 'Nenhuma.'}\n\n"
        f"Você recebeu: {fontes_txt}. Analise seguindo as regras do sistema."
    )


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
            raise TimeoutError("Tempo esgotado aguardando o processamento do áudio.")
        time.sleep(2)
        arquivo = client.files.get(name=arquivo.name)
    if arquivo.state == types.FileState.FAILED:
        raise RuntimeError("Falha no processamento remoto do arquivo de áudio.")
    return arquivo, arquivo


def _carregar_foto(caminho_foto: Path) -> Image.Image:
    """Abre a foto corrigindo a rotação EXIF (comum em fotos de celular)."""
    with Image.open(caminho_foto) as img:
        img.load()
        return ImageOps.exif_transpose(img).convert("RGB")


def _interpretar_resposta(response: types.GenerateContentResponse) -> AnaliseDiarioObra:
    if isinstance(response.parsed, AnaliseDiarioObra):
        return response.parsed
    if not response.text:
        raise ValueError("Resposta vazia do modelo (possível bloqueio de segurança).")
    return AnaliseDiarioObra.model_validate_json(response.text)


def _gerar_com_fallback(client: genai.Client, contents: list) -> AnaliseDiarioObra:
    """Percorre a cadeia de modelos com retry em erros transitórios (503 etc.).

    Erros de chave são propagados (não adianta trocar de modelo). Levanta RuntimeError
    somente se TODOS os modelos falharem.
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
_SINAIS_PARALISACAO = r"paramos|parou|paralis|suspens|n[aã]o\s+(?:trabalhamos|deu\s+para|foi\s+poss[ií]vel)|sem\s+trabalho"
_SINAIS_PENDENTE = r"\b(?:falta|faltou|precisa|amanh[aã]|vamos|vai\s+ser|pendente|ainda\s+n[aã]o|depois)\b"
def _tarefas_por_regras(obs: str, contexto_obra: str = "") -> list[ItemTarefa]:
    """Quebra a observação em atividades (por vírgula, ';', ' e ') e infere o ambiente.

    O ambiente vem do cômodo citado no trecho; sem cômodo, de um cômodo citado no resto do
    relato (serviços internos como cerâmica/piso/cano) ou do escopo da obra.
    """
    partes = re.split(r"[;,.]|\s+e\s+(?=\w+(?:ei|amos|ou|emos|imos|i)\b)", obs, flags=re.IGNORECASE)
    tarefas = []
    for parte in (p.strip() for p in partes):
        baixa = parte.lower()
        if (
            len(parte) < 6
            or any(re.search(padrao, baixa) for padrao, _ in _REGRAS_ATRASO)
            or re.search(_SINAIS_PARALISACAO, baixa)
        ):
            continue  # ignora fragmentos e frases que são atraso/paralisação, não atividade
        tarefas.append(
            ItemTarefa(
                ambiente=corrigir_ambiente("Geral", parte, obs, contexto_obra),
                descricao=parte[0].upper() + parte[1:],
                concluida=not re.search(_SINAIS_PENDENTE, parte.lower()),
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
) -> AnaliseDiarioObra:
    """Gera uma análise estruturada por regras locais, sem chamar a IA.

    `caminho_foto` aceita uma foto ou uma lista de fotos. As fotos não são classificadas
    aqui (fotos_classificadas fica vazia); quem salva usa as tarefas para vinculá-las.
    """
    fotos = _lista_fotos(caminho_foto)
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

    detalhes = [d for d in causas]
    if paralisou:
        detalhes.append("Houve paralisação dos serviços durante o dia")
    detalhes_txt = ("; ".join(detalhes) + ".") if detalhes else SEM_PARTICULARIDADES

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

    if fotos:
        qtd = f"{len(fotos)} fotos do dia arquivadas" if len(fotos) > 1 else "Foto do dia arquivada"
        asbuilt = (
            f"{qtd} como evidência As-Built. A descrição da posição de tubulações, conduítes e "
            "fiação pode ser completada na Galeria da Obra ou editando este registro."
        )
    else:
        asbuilt = "Sem foto para mapeamento As-Built"

    transcricao = (
        "Transcrição automática indisponível para este registro. Ouça o relato original no "
        "player ou edite o registro para incluir a transcrição."
        if tem_audio
        else ""
    )

    analise = AnaliseDiarioObra(
        transcricao_audio=transcricao,
        resumo_executivo=resumo,
        detalhes_execucao_imprevistos=detalhes_txt,
        motivo_atraso=motivo,
        mapeamento_asbuilt=asbuilt,
        tarefas_extraidas=_tarefas_por_regras(obs, contexto_obra),
    )
    analise._modo_offline = True
    analise._aviso = AVISO_ANALISE_SIMPLIFICADA
    return analise


# --------------------------------------------------------------------------- #
# Função principal
# --------------------------------------------------------------------------- #
def _corrigir_ambientes(analise: AnaliseDiarioObra, relato: str, contexto_obra: str, qtd_fotos: int) -> AnaliseDiarioObra:
    """Aplica as regras de palavras-chave sobre a saída da IA (ex.: cerâmica do banheiro
    classificada como 'Área Externa' volta para 'Banheiro') e descarta fotos inexistentes."""
    for t in analise.tarefas_extraidas:
        t.ambiente = corrigir_ambiente(t.ambiente, t.descricao, relato, contexto_obra)
    vistas: set[int] = set()
    fotos = []
    for f in analise.fotos_classificadas:
        if 1 <= f.indice_foto <= qtd_fotos and f.indice_foto not in vistas:
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
) -> AnaliseDiarioObra:
    """Analisa áudio + foto + observações de um dia de obra e devolve a análise validada.

    Tenta todos os modelos da cadeia (com retry em 503); só usa a análise offline se
    toda a cadeia falhar, se não houver chave no servidor ou se `modo_offline` for True.
    Nunca levanta exceção por indisponibilidade da IA — o registro sempre pode ser salvo.
    """
    audio_path = Path(caminho_audio) if caminho_audio else None
    fotos = _lista_fotos(caminho_foto)
    observacoes_texto = (observacoes_texto or "").strip()

    for p in (audio_path, *fotos):
        if p is not None and not p.is_file():
            raise FileNotFoundError(f"Arquivo não encontrado: {p}")
    if not (audio_path or fotos or observacoes_texto):
        raise ValueError("Envie pelo menos um áudio, uma foto ou observações em texto.")

    def offline() -> AnaliseDiarioObra:
        return gerar_analise_offline(observacoes_texto, contexto_obra, audio_path is not None, fotos)

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
        for i, foto in enumerate(fotos, start=1):
            contents += [f"FOTO {i}:", _carregar_foto(foto)]
        contents.append(
            _montar_prompt(contexto_obra, observacoes_texto, tem_audio=audio_path is not None, qtd_fotos=len(fotos))
        )
        analise = _gerar_com_fallback(client, contents)
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
