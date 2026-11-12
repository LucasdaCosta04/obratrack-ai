"""ObraTrack AI - interface Streamlit mobile-first."""

from __future__ import annotations

import base64
import html
import io
import uuid
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import streamlit as st
from PIL import Image, ImageOps

import database as db
from ai_service import processar_registro_obra

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = db.UPLOADS_DIR
SEM_ATRASO = "Sem atrasos registrados"

COR_PRODUTIVIDADE = {"Alta": "green", "Média": "orange", "Baixa": "red"}
ICONE_STATUS = {"Em Andamento": "🚧", "Pausada": "⏸️", "Concluída": "✅"}

st.set_page_config(
    page_title="ObraTrack AI",
    page_icon="🏗️",
    layout="centered",  # coluna única: lê melhor no celular do canteiro
    initial_sidebar_state="auto",
)


# --------------------------------------------------------------------------- #
# Inicialização
# --------------------------------------------------------------------------- #
@st.cache_resource
def _inicializar_banco() -> bool:
    db.init_db()
    db.seed_data()
    return True


_inicializar_banco()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def tem_atraso(motivo: str | None) -> bool:
    return bool(motivo and motivo.strip() and motivo.strip() != SEM_ATRASO)


def resolver_caminho(caminho: str | None) -> Path | None:
    """Caminhos são salvos relativos à raiz do projeto; resolve para absoluto."""
    if not caminho:
        return None
    p = Path(caminho)
    p = p if p.is_absolute() else BASE_DIR / p
    return p if p.is_file() else None


def salvar_upload(dados: bytes, obra_id: int, prefixo: str, sufixo: str) -> Path:
    """Salva bytes em uploads\\obra_<id>\\ com nome único e retorna o caminho relativo."""
    pasta = UPLOADS_DIR / f"obra_{obra_id}"
    pasta.mkdir(parents=True, exist_ok=True)
    nome = f"{prefixo}_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}{sufixo.lower()}"
    destino = pasta / nome
    destino.write_bytes(dados)
    return destino.relative_to(BASE_DIR)


def formatar_data(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return iso or "-"


def contexto_da_obra(obra: dict) -> str:
    return (
        f"Cliente/Obra: {obra['nome_cliente']}\n"
        f"Endereço: {obra.get('endereco') or 'não informado'}\n"
        f"Escopo: {obra.get('escopo_resumo') or 'não informado'}\n"
        f"Status: {obra['status']} | Início: {formatar_data(obra['data_inicio'])}"
    )


def metricas(registros: list[dict]) -> tuple[int, int, str]:
    total = len(registros)
    dias_atraso = len({r["data_registro"] for r in registros if tem_atraso(r["motivo_atraso"])})
    niveis = [r["nivel_produtividade"] for r in registros if r["nivel_produtividade"]]
    predominante = Counter(niveis).most_common(1)[0][0] if niveis else "—"
    return total, dias_atraso, predominante


def imagem_base64(caminho: Path, lado_max: int = 900) -> str | None:
    """Miniatura JPEG em base64 para embutir no relatório HTML."""
    try:
        with Image.open(caminho) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((lado_max, lado_max))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def gerar_relatorio_html(obra: dict, registros: list[dict]) -> str:
    """Relatório autocontido (fotos embutidas) para enviar ao cliente ou imprimir em PDF."""
    e = html.escape
    total, dias_atraso, predominante = metricas(registros)
    linhas_atraso = "".join(
        f"<tr><td>{e(formatar_data(r['data_registro']))}</td><td>{e(r['motivo_atraso'])}</td></tr>"
        for r in sorted(registros, key=lambda r: (r["data_registro"], r["id"]))
        if tem_atraso(r["motivo_atraso"])
    ) or f"<tr><td colspan='2'>{SEM_ATRASO} no período.</td></tr>"

    blocos = []
    for r in sorted(registros, key=lambda r: (r["data_registro"], r["id"])):
        foto = resolver_caminho(r["caminho_foto"])
        b64 = imagem_base64(foto) if foto else None
        img_tag = f"<img src='data:image/jpeg;base64,{b64}' alt='Foto do dia'>" if b64 else ""
        blocos.append(
            f"""<section class="dia">
  <h3>{e(formatar_data(r['data_registro']))} — Produtividade {e(r['nivel_produtividade'] or '-')}</h3>
  <p><strong>Serviços executados:</strong> {e(r['resumo_executivo'] or '-')}</p>
  {f"<p class='atraso'><strong>Justificativa de atraso:</strong> {e(r['motivo_atraso'])}</p>" if tem_atraso(r['motivo_atraso']) else ""}
  {img_tag}
  <p><strong>Registro As-Built:</strong> {e(r['mapeamento_asbuilt'] or '-')}</p>
</section>"""
        )

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relatório de Obra — {e(obra['nome_cliente'])}</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#1f2933}}
 h1{{margin-bottom:4px}} .meta{{color:#52606d;margin-top:0}}
 .kpis{{display:flex;gap:12px;flex-wrap:wrap}} .kpi{{border:1px solid #d9e2ec;border-radius:8px;padding:10px 14px}}
 table{{width:100%;border-collapse:collapse}} td{{border:1px solid #d9e2ec;padding:6px 8px;vertical-align:top}}
 .dia{{border-top:1px solid #d9e2ec;padding-top:8px;margin-top:16px;page-break-inside:avoid}}
 .atraso{{background:#fff4e5;border-left:4px solid #f0a020;padding:6px 10px}}
 img{{max-width:100%;border-radius:6px}}
</style></head><body>
<h1>Relatório de Obra</h1>
<p class="meta"><strong>{e(obra['nome_cliente'])}</strong> · {e(obra.get('endereco') or '')}<br>
Escopo: {e(obra.get('escopo_resumo') or '-')}<br>
Status: {e(obra['status'])} · Início: {e(formatar_data(obra['data_inicio']))} · Emitido em {date.today():%d/%m/%Y}</p>
<div class="kpis">
 <div class="kpi">Registros: <strong>{total}</strong></div>
 <div class="kpi">Dias com atraso justificado: <strong>{dias_atraso}</strong></div>
 <div class="kpi">Produtividade predominante: <strong>{e(predominante)}</strong></div>
</div>
<h2>Justificativas de atraso</h2>
<table><tr><td><strong>Data</strong></td><td><strong>Motivo</strong></td></tr>{linhas_atraso}</table>
<h2>Diário e documentação As-Built</h2>
{''.join(blocos) or '<p>Nenhum registro.</p>'}
</body></html>"""


# --------------------------------------------------------------------------- #
# Sidebar: cadastro de Bloco de Cliente/Obra
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("➕ Novo Bloco de Cliente/Obra")
    with st.form("form_nova_obra", clear_on_submit=True):
        nome = st.text_input("Nome da Cliente / Obra *", placeholder="Ex.: Reforma Cozinha - Sra. Ana")
        endereco = st.text_input("Endereço")
        escopo = st.text_area("Escopo da Obra", placeholder="Serviços contratados, metragens, materiais...")
        status = st.selectbox("Status", db.STATUS_OBRA)
        inicio = st.date_input("Data de início", value=date.today(), format="DD/MM/YYYY")
        criar = st.form_submit_button("Cadastrar Bloco", type="primary", width="stretch")

    if criar:
        if not nome.strip():
            st.error("Informe o nome da cliente/obra.")
        else:
            novo_id = db.criar_obra(nome.strip(), endereco.strip(), escopo.strip(), status, inicio.isoformat())
            st.session_state["obra_id"] = novo_id
            st.session_state["flash"] = f"Bloco “{nome.strip()}” cadastrado."
            st.rerun()

    st.divider()
    st.caption("ObraTrack AI · Diário de obra com IA multimodal (Gemini 2.5 Flash)")


# --------------------------------------------------------------------------- #
# Tela principal: seleção de Blocos
# --------------------------------------------------------------------------- #
st.title("🏗️ ObraTrack AI")

if flash := st.session_state.pop("flash", None):
    st.success(flash)

obras = db.listar_obras()
if not obras:
    st.info("Nenhuma obra cadastrada. Use a barra lateral para criar o primeiro Bloco.")
    st.stop()

ids_validos = {o["id"] for o in obras}
if st.session_state.get("obra_id") not in ids_validos:
    st.session_state["obra_id"] = obras[0]["id"]

st.subheader("Blocos de Clientes/Obras")
colunas = st.columns(2)  # em telas estreitas o Streamlit empilha as colunas
for i, o in enumerate(obras):
    selecionada = o["id"] == st.session_state["obra_id"]
    with colunas[i % 2].container(border=True):
        st.markdown(f"**{ICONE_STATUS.get(o['status'], '')} {o['nome_cliente']}**")
        st.caption(o.get("endereco") or "Endereço não informado")
        if st.button(
            "✔ Selecionado" if selecionada else "Abrir bloco",
            key=f"abrir_{o['id']}",
            type="primary" if selecionada else "secondary",
            disabled=selecionada,
            width="stretch",
        ):
            st.session_state["obra_id"] = o["id"]
            st.rerun()

obra = next(o for o in obras if o["id"] == st.session_state["obra_id"])
obra_id = obra["id"]
registros = db.listar_registros_por_obra(obra_id)

# Cabeçalho do bloco selecionado
st.divider()
st.header(obra["nome_cliente"])
st.markdown(
    f"📍 {obra.get('endereco') or 'Endereço não informado'}  \n"
    f"{ICONE_STATUS.get(obra['status'], '')} **{obra['status']}** · Início em {formatar_data(obra['data_inicio'])}"
)
if obra.get("escopo_resumo"):
    st.caption(f"Escopo: {obra['escopo_resumo']}")

total, dias_atraso, predominante = metricas(registros)
m1, m2, m3 = st.columns(3)
m1.metric("Total de Registros", total, border=True)
m2.metric("Dias c/ Atraso Justificado", dias_atraso, border=True)
m3.metric("Produtividade Predominante", predominante, border=True)

aba_registro, aba_historico = st.tabs(
    ["🎙️ Registrar Dia (Áudio e Foto)", "📚 Histórico, Produtividade e Documentação As-Built"]
)


def exibir_resultado(analise: dict, foto: Path | None) -> None:
    nivel = analise["nivel_produtividade"]
    st.badge(f"Produtividade {nivel}", color=COR_PRODUTIVIDADE.get(nivel, "gray"))
    if tem_atraso(analise["motivo_atraso"]):
        st.warning(f"**Justificativa de atraso:** {analise['motivo_atraso']}", icon="⚠️")
    st.markdown(f"**Resumo executivo:** {analise['resumo_executivo']}")
    if foto:
        c_foto, c_txt = st.columns([1, 1])
        c_foto.image(str(foto), width="stretch")
        c_txt.markdown(f"**Mapeamento As-Built:**\n\n{analise['mapeamento_asbuilt']}")
    else:
        st.markdown(f"**Mapeamento As-Built:** {analise['mapeamento_asbuilt']}")
    if analise.get("transcricao_audio"):
        with st.expander("Transcrição do áudio"):
            st.write(analise["transcricao_audio"])


# --------------------------------------------------------------------------- #
# Aba 1: Registrar Dia
# --------------------------------------------------------------------------- #
with aba_registro:
    with st.form(f"form_registro_{obra_id}", clear_on_submit=True):
        audio = st.audio_input("Gravar relato do dia no canteiro")
        foto_up = st.file_uploader("Adicionar foto da reforma / tubulação", type=["jpg", "jpeg", "png"])
        obs = st.text_area("Observações (opcional)", placeholder="Ex.: faltou 1 pedreiro; choveu das 14h às 16h.")
        data_reg = st.date_input("Data do registro", value=date.today(), format="DD/MM/YYYY")
        enviar = st.form_submit_button("Processar com IA e Salvar no Bloco", type="primary", width="stretch")

    if enviar:
        if not (audio or foto_up or obs.strip()):
            st.error("Grave um áudio, envie uma foto ou escreva uma observação.")
        else:
            salvos: list[Path] = []
            try:
                caminho_audio = caminho_foto = None
                if audio:
                    caminho_audio = salvar_upload(audio.getvalue(), obra_id, "audio", ".wav")
                    salvos.append(caminho_audio)
                if foto_up:
                    caminho_foto = salvar_upload(
                        foto_up.getvalue(), obra_id, "foto", Path(foto_up.name).suffix or ".jpg"
                    )
                    salvos.append(caminho_foto)

                with st.spinner("Analisando áudio, foto e contexto da obra com IA..."):
                    analise = processar_registro_obra(
                        caminho_audio=BASE_DIR / caminho_audio if caminho_audio else None,
                        caminho_foto=BASE_DIR / caminho_foto if caminho_foto else None,
                        observacoes_texto=obs,
                        contexto_obra=contexto_da_obra(obra),
                    ).model_dump()

                db.inserir_registro(
                    obra_id=obra_id,
                    data_registro=data_reg.isoformat(),
                    caminho_foto=caminho_foto,
                    **analise,
                )
            except Exception as exc:  # mostra o erro na tela e não deixa arquivos órfãos
                for p in salvos:
                    (BASE_DIR / p).unlink(missing_ok=True)
                st.error(f"Não foi possível processar o registro: {exc}")
            else:
                st.success("Registro salvo no bloco da obra.")
                exibir_resultado(analise, BASE_DIR / caminho_foto if caminho_foto else None)
                registros = db.listar_registros_por_obra(obra_id)


# --------------------------------------------------------------------------- #
# Aba 2: Histórico e As-Built
# --------------------------------------------------------------------------- #
with aba_historico:
    if not registros:
        st.info("Nenhum registro ainda. Use a aba “Registrar Dia” para criar o primeiro.")
    else:
        with st.expander("📄 Gerar Relatório para o Cliente"):
            atrasos = [r for r in registros if tem_atraso(r["motivo_atraso"])]
            st.markdown("#### Justificativas de atraso")
            if atrasos:
                for r in sorted(atrasos, key=lambda r: (r["data_registro"], r["id"])):
                    st.markdown(f"- **{formatar_data(r['data_registro'])}:** {r['motivo_atraso']}")
            else:
                st.markdown(f"_{SEM_ATRASO} no período._")

            fotos = [(r, p) for r in registros if (p := resolver_caminho(r["caminho_foto"]))]
            if fotos:
                st.markdown("#### Registro fotográfico")
                g = st.columns(3)
                for i, (r, p) in enumerate(fotos):
                    g[i % 3].image(str(p), caption=formatar_data(r["data_registro"]), width="stretch")

            st.download_button(
                "⬇️ Baixar relatório (HTML para enviar ou imprimir em PDF)",
                data=gerar_relatorio_html(obra, registros),
                file_name=f"relatorio_obra_{obra_id}_{date.today():%Y%m%d}.html",
                mime="text/html",
                width="stretch",
                on_click="ignore",
            )

        for r in registros:  # já vem do mais recente para o mais antigo
            with st.container(border=True):
                topo_data, topo_badge = st.columns([3, 2])
                topo_data.markdown(f"**📅 {formatar_data(r['data_registro'])}**")
                with topo_badge:
                    nivel = r["nivel_produtividade"] or "—"
                    st.badge(f"Produtividade {nivel}", color=COR_PRODUTIVIDADE.get(nivel, "gray"))

                if tem_atraso(r["motivo_atraso"]):
                    st.warning(f"**Justificativa de atraso:** {r['motivo_atraso']}", icon="⚠️")

                st.markdown(f"**Resumo executivo:** {r['resumo_executivo'] or '-'}")

                foto = resolver_caminho(r["caminho_foto"])
                if foto:
                    c_foto, c_txt = st.columns([1, 1])
                    c_foto.image(str(foto), width="stretch")
                    c_txt.markdown(f"**Mapeamento As-Built:**\n\n{r['mapeamento_asbuilt'] or '-'}")
                elif r["mapeamento_asbuilt"]:
                    st.markdown(f"**Mapeamento As-Built:** {r['mapeamento_asbuilt']}")

                if r["transcricao_audio"]:
                    with st.expander("Transcrição do áudio"):
                        st.write(r["transcricao_audio"])
