"""Segurança e privacidade do ObraTrack AI.

- Remoção de metadados EXIF (GPS, data/hora original, modelo do celular) das fotos.
- Validação de mídia por assinatura real do arquivo (magic bytes) e limite de tamanho.
- Armazenamento com nome aleatório (UUID4), sem nomes de clientes, nomes originais ou datas.
- Higienização de textos digitados pelo usuário.
- PIN de acesso opcional com bloqueio temporário após tentativas erradas.
"""

from __future__ import annotations

import hmac
import html
import io
import os
import re
import threading
import time
import uuid
import warnings
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from PIL import Image, ImageOps

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"

TAMANHO_MAXIMO_BYTES = 15 * 1024 * 1024  # 15 MB por arquivo
QUALIDADE_JPEG = 90

LIMITE_TEXTO_PADRAO = 2_000
LIMITE_TEXTO_LONGO = 20_000  # transcrições e textos gerados pela IA

MAX_TENTATIVAS_PIN = 5
BLOQUEIO_PIN_SEGUNDOS = 5 * 60

# Caracteres de controle (exceto \t e \n), incluindo NUL, que podem corromper logs e HTML.
_CONTROLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class MidiaInvalidaError(ValueError):
    """Arquivo recusado. A mensagem é segura para ser exibida ao usuário."""


# --------------------------------------------------------------------------- #
# Imagens: remoção de metadados
# --------------------------------------------------------------------------- #
def imagem_sem_metadados(img: Image.Image) -> Image.Image:
    """Nova imagem RGB construída só com os pixels: nenhum EXIF, XMP, ICC ou outro metadado.

    A rotação indicada pelo EXIF é aplicada aos pixels antes, para a foto não ficar deitada.
    """
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        fundo = Image.new("RGB", img.size, (255, 255, 255))
        fundo.paste(img, mask=img.getchannel("A"))
        img = fundo
    else:
        img = img.convert("RGB")
    # frombytes cria um objeto novo, com `info` vazio: nada do arquivo original é copiado.
    return Image.frombytes("RGB", img.size, img.tobytes())


def strip_image_exif(image_bytes: bytes) -> bytes:
    """Regrava a imagem em memória como JPEG limpo, sem nenhum metadado (GPS, data, aparelho)."""
    try:
        with warnings.catch_warnings():
            # Imagem gigante (possível "decompression bomb") vira erro em vez de aviso.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes)) as original:
                original.load()
                limpa = imagem_sem_metadados(original)
    except MidiaInvalidaError:
        raise
    except Exception as exc:
        raise MidiaInvalidaError("Não foi possível ler a foto. Envie uma imagem JPG ou PNG válida.") from exc
    buf = io.BytesIO()
    limpa.save(buf, format="JPEG", quality=QUALIDADE_JPEG, optimize=True)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Validação e armazenamento de mídia
# --------------------------------------------------------------------------- #
def _tipo_audio(cabecalho: bytes) -> str | None:
    if cabecalho[:4] == b"RIFF" and cabecalho[8:12] == b"WAVE":
        return ".wav"
    if cabecalho[:4] == b"OggS":
        return ".ogg"
    if cabecalho[:3] == b"ID3":
        return ".mp3"
    # MP3 sem tag ID3: começa direto num frame (sync de 11 bits) de MPEG-1/2 Layer III.
    if len(cabecalho) >= 2 and cabecalho[0] == 0xFF and (cabecalho[1] & 0xE6) == 0xE2:
        return ".mp3"
    return None


def _tipo_imagem(cabecalho: bytes) -> str | None:
    if cabecalho[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if cabecalho[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return None


def validate_and_save_media(file_bytes: bytes, media_type: str) -> Path:
    """Valida e salva uma mídia em uploads\\<uuid4>.<ext>.

    `media_type` é "audio" (WAV, MP3 ou OGG) ou "image" (JPEG ou PNG). O tipo é decidido
    pelo conteúdo real do arquivo, nunca pelo nome ou extensão enviados. Fotos são salvas
    sempre como JPEG sem metadados. Retorna o caminho relativo à pasta do projeto,
    no formato gravado no banco (ex.: uploads\\3f2a...e1.jpg).
    """
    if media_type not in ("audio", "image"):
        raise ValueError(f"media_type inválido: {media_type!r}")
    if not file_bytes:
        raise MidiaInvalidaError("O arquivo enviado está vazio.")
    if len(file_bytes) > TAMANHO_MAXIMO_BYTES:
        limite_mb = TAMANHO_MAXIMO_BYTES // (1024 * 1024)
        raise MidiaInvalidaError(f"Arquivo muito grande. O limite é {limite_mb} MB.")

    cabecalho = file_bytes[:16]
    if media_type == "audio":
        extensao = _tipo_audio(cabecalho)
        if extensao is None:
            raise MidiaInvalidaError("Formato de áudio não reconhecido. Use WAV, MP3 ou OGG.")
        conteudo = file_bytes
    else:
        if _tipo_imagem(cabecalho) is None:
            raise MidiaInvalidaError("Formato de foto não reconhecido. Use JPG ou PNG.")
        conteudo = strip_image_exif(file_bytes)
        extensao = ".jpg"

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    destino = UPLOADS_DIR / f"{uuid.uuid4().hex}{extensao}"
    # "xb": falha em vez de sobrescrever caso o nome já exista.
    with open(destino, "xb") as f:
        f.write(conteudo)
    return destino.relative_to(BASE_DIR)


# --------------------------------------------------------------------------- #
# Textos
# --------------------------------------------------------------------------- #
def sanitize_text(text: object, max_length: int = LIMITE_TEXTO_PADRAO, escape_html: bool = False) -> str:
    """strip, remoção de caracteres de controle e limite de tamanho.

    Com `escape_html=True` (texto que vai para HTML customizado) aplica `html.escape`
    depois de cortar, para não deixar entidades HTML pela metade.
    """
    texto = _CONTROLE.sub("", str(text or "")).strip()
    if len(texto) > max_length:
        texto = texto[:max_length].rstrip()
    return html.escape(texto, quote=True) if escape_html else texto


# --------------------------------------------------------------------------- #
# PIN de acesso
# --------------------------------------------------------------------------- #
class _RegistroTentativas:
    """Contadores de PIN errado compartilhados por todas as sessões do servidor:
    recarregar a página ou abrir outra aba não zera as tentativas."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.dados: dict[str, dict[str, float]] = {}


@st.cache_resource(show_spinner=False)
def _registro() -> _RegistroTentativas:
    return _RegistroTentativas()


def _pin_configurado() -> str:
    load_dotenv(BASE_DIR / ".env", override=False)
    return (os.environ.get("APP_PIN") or "").strip()


def pin_ativo() -> bool:
    return bool(_pin_configurado())


def _origem_cliente() -> str:
    """Identifica quem tenta o PIN (IP quando disponível) para o bloqueio temporário."""
    try:
        ip = st.context.ip_address
    except Exception:
        ip = None
    return ip if isinstance(ip, str) and ip else "local"


def _segundos_bloqueado(origem: str) -> int:
    reg = _registro()
    with reg.lock:
        estado = reg.dados.get(origem)
        if not estado or not estado.get("bloqueado_ate"):
            return 0
        restante = estado["bloqueado_ate"] - time.monotonic()
        if restante <= 0:
            reg.dados.pop(origem, None)  # bloqueio expirou: recomeça a contagem
            return 0
        return int(restante + 0.999)


def _registrar_falha(origem: str) -> int:
    """Conta a falha; ao chegar no limite, bloqueia. Retorna as tentativas restantes."""
    reg = _registro()
    with reg.lock:
        estado = reg.dados.setdefault(origem, {"falhas": 0, "bloqueado_ate": 0})
        estado["falhas"] += 1
        restantes = MAX_TENTATIVAS_PIN - int(estado["falhas"])
        if restantes <= 0:
            estado["bloqueado_ate"] = time.monotonic() + BLOQUEIO_PIN_SEGUNDOS
        return max(0, restantes)


def _limpar_falhas(origem: str) -> None:
    reg = _registro()
    with reg.lock:
        reg.dados.pop(origem, None)


def check_auth() -> bool:
    """Exige o PIN de acesso, se configurado no servidor, antes de exibir qualquer dado.

    Sem PIN configurado, libera o acesso. Com PIN, mostra a tela de acesso e interrompe
    o script (st.stop) até a sessão ser autenticada. Após 5 erros a origem fica
    bloqueada por 5 minutos, mesmo recarregando a página.
    """
    pin = _pin_configurado()
    if not pin or st.session_state.get("authenticated"):
        return True

    origem = _origem_cliente()
    st.markdown("## 🔒 Acesso restrito")
    st.caption("Digite o PIN de acesso para ver os dados das obras.")

    def aviso_bloqueio(segundos: int) -> None:
        st.error(f"Muitas tentativas incorretas. Tente novamente em {max(1, -(-segundos // 60))} min.", icon="⛔")

    if bloqueado := _segundos_bloqueado(origem):
        aviso_bloqueio(bloqueado)
        st.stop()

    with st.form("form_pin", clear_on_submit=True, enter_to_submit=True):
        digitado = st.text_input("PIN", type="password", max_chars=32, help="PIN fornecido pelo responsável da obra.")
        entrar = st.form_submit_button("Entrar", type="primary", width="stretch")

    if entrar:
        if hmac.compare_digest(digitado.strip().encode("utf-8"), pin.encode("utf-8")):
            _limpar_falhas(origem)
            st.session_state.authenticated = True
            st.rerun()
        if restantes := _registrar_falha(origem):
            st.error(f"PIN incorreto. {restantes} tentativa(s) restante(s).", icon="⚠️")
        else:
            aviso_bloqueio(BLOQUEIO_PIN_SEGUNDOS)
    st.stop()
    return False  # inalcançável: st.stop() interrompe o script


def logout() -> None:
    st.session_state.authenticated = False
