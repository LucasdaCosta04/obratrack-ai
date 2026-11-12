"""ObraTrack AI - interface Streamlit mobile-first."""

from __future__ import annotations

import base64
import html
import io
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import streamlit as st
from PIL import Image, ImageOps

import database as db
from ai_service import (
    MODELO,
    SEM_ATRASO,
    chave_configurada,
    gerar_analise_demo,
    processar_registro_obra,
)

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = db.UPLOADS_DIR

COR_PRODUTIVIDADE = {"Alta": "green", "Média": "orange", "Baixa": "red"}
ICONE_PRODUTIVIDADE = {"Alta": "🟢", "Média": "🟡", "Baixa": "🔴"}
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
    db.init_db()  # cria tabelas e aplica migrações (ex.: coluna caminho_audio)
    db.seed_data()
    return True


_inicializar_banco()
st.session_state.setdefault("modo_demo", False)
CHAVE_OK = chave_configurada()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def tem_atraso(motivo: str | None) -> bool:
    return bool(motivo and motivo.strip() and motivo.strip() != SEM_ATRASO)


def resolver_caminho(caminho: str | None) -> Path | None:
    """Resolve o caminho salvo no banco; só exibe arquivos existentes dentro de uploads\\."""
    p = db.caminho_midia_seguro(caminho)
    return p if p and p.is_file() else None


def salvar_upload(dados: bytes, prefixo: str, sufixo: str) -> Path:
    """Salva em uploads\\<prefixo>_<timestamp><sufixo> e retorna o caminho relativo ao projeto."""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    destino = UPLOADS_DIR / f"{prefixo}_{datetime.now():%Y%m%d_%H%M%S_%f}{sufixo.lower()}"
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
    cronologicos = sorted(registros, key=lambda r: (r["data_registro"], r["id"]))
    linhas_atraso = "".join(
        f"<tr><td>{e(formatar_data(r['data_registro']))}</td><td>{e(r['motivo_atraso'])}</td></tr>"
        for r in cronologicos
        if tem_atraso(r["motivo_atraso"])
    ) or f"<tr><td colspan='2'>{SEM_ATRASO} no período.</td></tr>"

    blocos = []
    for r in cronologicos:
        foto = resolver_caminho(r["caminho_foto"])
        b64 = imagem_base64(foto) if foto else None
        img_tag = f"<img src='data:image/jpeg;base64,{b64}' alt='Foto do dia'>" if b64 else ""
        atraso = (
            f"<p class='atraso'><strong>Justificativa de atraso:</strong> {e(r['motivo_atraso'])}</p>"
            if tem_atraso(r["motivo_atraso"])
            else ""
        )
        blocos.append(
            f"""<section class="dia">
  <h3>{e(formatar_data(r['data_registro']))} — Produtividade {e(r['nivel_produtividade'] or '-')}</h3>
  <p><strong>Serviços executados:</strong> {e(r['resumo_executivo'] or '-')}</p>
  {atraso}
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


def card_registro(r: dict) -> None:
    """Card de um registro: data, badge, áudio, atraso, resumo, foto + As-Built, transcrição."""
    with st.container(border=True):
        nivel = r.get("nivel_produtividade") or "—"
        topo_data, topo_badge = st.columns([3, 2], vertical_alignment="center")
        topo_data.markdown(f"**📅 {formatar_data(r['data_registro'])}**")
        with topo_badge:
            st.badge(
                f"{ICONE_PRODUTIVIDADE.get(nivel, '')} Produtividade {nivel}",
                color=COR_PRODUTIVIDADE.get(nivel, "gray"),
                help="Avaliação das entregas do dia frente aos imprevistos relatados.",
            )

        audio = resolver_caminho(r.get("caminho_audio"))
        if audio:
            st.caption("🎙️ Relato original do canteiro")
            st.audio(str(audio), format="audio/wav")

        if tem_atraso(r.get("motivo_atraso")):
            st.warning(f"**Justificativa de atraso:** {r['motivo_atraso']}", icon="⚠️")
        else:
            st.caption(f"✅ {SEM_ATRASO}")

        st.markdown(f"**Resumo executivo:** {r.get('resumo_executivo') or '-'}")

        foto = resolver_caminho(r.get("caminho_foto"))
        if foto:
            c_foto, c_txt = st.columns([1, 1])
            c_foto.image(str(foto), width="stretch")
            c_txt.markdown(f"**📐 Mapeamento As-Built:**\n\n{r.get('mapeamento_asbuilt') or '-'}")
        elif r.get("mapeamento_asbuilt"):
            st.markdown(f"**📐 Mapeamento As-Built:** {r['mapeamento_asbuilt']}")

        if r.get("transcricao_audio"):
            with st.expander("📝 Transcrição do áudio"):
                st.write(r["transcricao_audio"])


# --------------------------------------------------------------------------- #
# Sidebar: cadastro de Bloco de Cliente/Obra + configurações da IA
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("➕ Novo Bloco de Cliente/Obra")
    with st.form("form_nova_obra", clear_on_submit=True, enter_to_submit=False):
        nome = st.text_input(
            "Nome da Cliente / Obra *",
            placeholder="Ex.: Reforma Cozinha - Sra. Ana",
            help="Nome que identifica o bloco na tela principal. Use o padrão 'Serviço - Cliente'.",
        )
        endereco = st.text_input(
            "Endereço",
            placeholder="Rua, número - bairro",
            help="Endereço da obra. Aparece no cabeçalho do bloco e no relatório do cliente.",
        )
        escopo = st.text_area(
            "Escopo da Obra",
            placeholder="Serviços contratados",
            help="Serviços, metragens e materiais contratados. A IA usa este escopo para avaliar a produtividade do dia.",
        )
        status = st.selectbox(
            "Status",
            db.STATUS_OBRA,
            help="Situação atual da obra. Pode ser cadastrada já pausada ou concluída.",
        )
        inicio = st.date_input(
            "Data de início",
            value=date.today(),
            format="DD/MM/YYYY",
            help="Data em que a obra começou (ou começará).",
        )
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
    # A chave da API é configurada apenas no servidor; a interface só mostra o status.
    with st.expander("⚙️ Configurações da IA"):
        if st.session_state["modo_demo"]:
            st.caption("🧪 Modo Demonstração ativo: nenhuma chamada à IA será feita.")
        elif CHAVE_OK:
            st.caption(f"✅ IA conectada ao servidor ({MODELO}).")
        else:
            st.caption("⚠️ IA indisponível: chave não configurada no servidor.")

        st.checkbox(
            "Usar Modo Demonstração (Sem chave de API)",
            key="modo_demo",
            help="Gera a análise por regras locais a partir das observações, sem chamar o Gemini. Ideal para testar a interface.",
        )

    st.caption("ObraTrack AI · Diário de obra com IA multimodal (Gemini 3.8 Flash)")


# --------------------------------------------------------------------------- #
# Tela principal: seleção de Blocos
# --------------------------------------------------------------------------- #
st.title("🏗️ ObraTrack AI")

if not CHAVE_OK and not st.session_state["modo_demo"]:
    st.error(
        "**IA não configurada no servidor.** Administrador: defina a variável de ambiente "
        "`GEMINI_API_KEY` (ou crie o arquivo `.env` na raiz do projeto a partir do `.env.example`) "
        "e reinicie a aplicação. Até lá, os registros são salvos em Modo Demonstração.",
        icon="🔒",
    )

if flash := st.session_state.pop("flash", None):
    st.success(flash)

obras = db.listar_obras()
if not obras:
    st.info("Nenhuma obra cadastrada. Use a barra lateral para criar o primeiro Bloco.")
    st.stop()

ids_validos = {o["id"] for o in obras}
if st.session_state.get("obra_id") not in ids_validos:
    st.session_state["obra_id"] = obras[0]["id"]

st.subheader("Blocos de Clientes/Obras", help="Toque em um bloco para abrir o diário daquela cliente.")
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

# Cabeçalho do bloco selecionado
st.divider()
st.header(obra["nome_cliente"])
st.markdown(
    f"📍 {obra.get('endereco') or 'Endereço não informado'}  \n"
    f"{ICONE_STATUS.get(obra['status'], '')} **{obra['status']}** · Início em {formatar_data(obra['data_inicio'])}"
)
if obra.get("escopo_resumo"):
    st.caption(f"Escopo: {obra['escopo_resumo']}")

# As métricas são desenhadas no fim do script, para já refletirem um registro recém-salvo.
area_metricas = st.container()

aba_registro, aba_historico = st.tabs(
    ["🎙️ Registrar Dia (Áudio e Foto)", "📚 Histórico, Produtividade e Documentação As-Built"]
)


# --------------------------------------------------------------------------- #
# Aba 1: Registrar Dia
# --------------------------------------------------------------------------- #
with aba_registro:
    # enter_to_submit=False: Enter num campo não dispara o processamento sem querer.
    with st.form(f"form_registro_{obra_id}", clear_on_submit=True, enter_to_submit=False):
        audio = st.audio_input(
            "Gravar relato do dia no canteiro",
            help="Toque no microfone e conte o que foi feito, o que atrasou (chuva, faltas, material) "
            "e onde passaram canos/fios. A IA transcreve e extrai tudo da sua voz.",
        )
        foto_up = st.file_uploader(
            "Adicionar foto da reforma / tubulação",
            type=["jpg", "jpeg", "png"],
            help="Fotografe a parede/piso ANTES do reboco ou revestimento, com uma referência de medida. "
            "A IA descreve a posição das tubulações para o As-Built.",
        )
        c_obs, c_data = st.columns([2, 1])
        obs = c_obs.text_input(
            "Observações rápidas (opcional)",
            placeholder="Ex: faltou 1 pedreiro",
            help="Digite uma nota curta ou deixe em branco e fale tudo no áudio.",
        )
        data_reg = c_data.date_input(
            "Data do registro",
            value=date.today(),
            format="DD/MM/YYYY",
            help="Dia a que o relato se refere. Altere para lançar um dia anterior.",
        )
        enviar = st.form_submit_button("Processar com IA e Salvar no Bloco", type="primary", width="stretch")

    if enviar:
        if not (audio or foto_up or obs.strip()):
            st.error("Grave um áudio, envie uma foto ou escreva uma observação.")
        else:
            caminho_audio = salvar_upload(audio.getvalue(), "audio", ".wav") if audio else None
            caminho_foto = (
                salvar_upload(foto_up.getvalue(), "foto", Path(foto_up.name).suffix or ".jpg") if foto_up else None
            )
            audio_abs = BASE_DIR / caminho_audio if caminho_audio else None
            foto_abs = BASE_DIR / caminho_foto if caminho_foto else None

            rotulo = "Gerando análise em Modo Demonstração..." if st.session_state["modo_demo"] else (
                "Transcrevendo o áudio e analisando a foto com IA..."
            )
            with st.spinner(rotulo):
                try:
                    analise = processar_registro_obra(
                        caminho_audio=audio_abs,
                        caminho_foto=foto_abs,
                        observacoes_texto=obs,
                        contexto_obra=contexto_da_obra(obra),
                        modo_demo=st.session_state["modo_demo"],
                    )
                except Exception as exc:  # nunca perde o registro: cai no modo demonstração
                    analise = gerar_analise_demo(
                        obs, contexto_da_obra(obra), audio_abs is not None, foto_abs,
                        aviso=f"Falha ao consultar a IA ({exc}). Registro salvo em Modo Demonstração.",
                    )

            dados = analise.model_dump()
            novo_id = db.inserir_registro(
                obra_id=obra_id,
                data_registro=data_reg.isoformat(),
                caminho_foto=caminho_foto,
                caminho_audio=caminho_audio,
                **dados,
            )

            st.success("Registro salvo com sucesso no bloco da cliente!")
            if analise.modo_demo:
                st.info(analise.aviso, icon="🧪")
            card_registro(
                {
                    "id": novo_id,
                    "data_registro": data_reg.isoformat(),
                    "caminho_foto": str(caminho_foto) if caminho_foto else None,
                    "caminho_audio": str(caminho_audio) if caminho_audio else None,
                    **dados,
                }
            )

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
                help="Arquivo único com fotos embutidas. Abra no navegador e use Ctrl+P para salvar em PDF.",
            )

        for r in registros:  # já vem do mais recente para o mais antigo
            card_registro(r)


# --------------------------------------------------------------------------- #
# Métricas do bloco (desenhadas por último, no espaço reservado acima das abas)
# --------------------------------------------------------------------------- #
total, dias_atraso, predominante = metricas(registros)
with area_metricas:
    m1, m2, m3 = st.columns(3)
    m1.metric(
        "Total de Registros", total, border=True,
        help="Quantidade de dias/relatos registrados neste bloco.",
    )
    m2.metric(
        "Dias c/ Atraso Justificado", dias_atraso, border=True,
        help="Dias distintos com chuva, falta de funcionário, material ou problema técnico documentado. "
        "Base para pedido de aditivo de prazo.",
    )
    m3.metric(
        "Produtividade Predominante",
        f"{ICONE_PRODUTIVIDADE.get(predominante, '')} {predominante}".strip(),
        border=True,
        help="Nível de produtividade mais frequente entre os registros da obra.",
    )
