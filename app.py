"""ObraTrack AI - interface Streamlit mobile-first."""

from __future__ import annotations

import base64
import html
import io
import logging
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import streamlit as st
from PIL import Image, ImageOps

import database as db
from ai_service import SEM_ATRASO, gerar_analise_offline, processar_registro_obra

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = db.UPLOADS_DIR

NIVEIS = ("Alta", "Média", "Baixa")
COR_PRODUTIVIDADE = {"Alta": "green", "Média": "orange", "Baixa": "red"}
ICONE_PRODUTIVIDADE = {"Alta": "🟢", "Média": "🟡", "Baixa": "🔴"}
ICONE_STATUS = {"Em Andamento": "🚧", "Pausada": "⏸️", "Concluída": "✅"}
AMBIENTES_SUGERIDOS = [
    "Geral", "Banheiro Social", "Banheiro Suíte", "Lavabo", "Cozinha", "Área de Serviço",
    "Sala", "Quarto", "Varanda", "Garagem", "Fachada", "Muro", "Calçada", "Cobertura",
]

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
def _inicializar() -> bool:
    # Detalhes técnicos (ex.: falhas da IA) vão para o log do servidor, nunca para a tela.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init_db()  # cria tabelas e aplica migrações
    db.seed_data()
    return True


_inicializar()


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


def para_data(iso: str) -> date:
    try:
        return date.fromisoformat(iso)
    except (TypeError, ValueError):
        return date.today()


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


def agrupar_por_ambiente(tarefas: list[dict]) -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = {}
    for t in tarefas:
        grupos.setdefault(t["ambiente"] or "Geral", []).append(t)
    return grupos


def flash(mensagem: str) -> None:
    """Mensagem exibida como toast na próxima execução (sobrevive ao st.rerun)."""
    st.session_state["flash"] = mensagem


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


def gerar_relatorio_html(obra: dict, registros: list[dict], tarefas: list[dict]) -> str:
    """Relatório autocontido (fotos embutidas) para enviar ao cliente ou imprimir em PDF."""
    e = html.escape
    total, dias_atraso, predominante = metricas(registros)
    cronologicos = sorted(registros, key=lambda r: (r["data_registro"], r["id"]))
    linhas_atraso = "".join(
        f"<tr><td>{e(formatar_data(r['data_registro']))}</td><td>{e(r['motivo_atraso'])}</td></tr>"
        for r in cronologicos
        if tem_atraso(r["motivo_atraso"])
    ) or f"<tr><td colspan='2'>{SEM_ATRASO} no período.</td></tr>"

    concluidas = sum(t["concluida"] for t in tarefas)
    checklist = "".join(
        f"<h3>{e(amb)} ({sum(t['concluida'] for t in itens)}/{len(itens)})</h3><ul>"
        + "".join(f"<li>{'☑' if t['concluida'] else '☐'} {e(t['descricao'])}</li>" for t in itens)
        + "</ul>"
        for amb, itens in agrupar_por_ambiente(tarefas).items()
    ) or "<p>Nenhuma tarefa registrada.</p>"

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
 ul{{list-style:none;padding-left:4px}} li{{margin:2px 0}}
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
 <div class="kpi">Tarefas concluídas: <strong>{concluidas}/{len(tarefas)}</strong></div>
</div>
<h2>Justificativas de atraso</h2>
<table><tr><td><strong>Data</strong></td><td><strong>Motivo</strong></td></tr>{linhas_atraso}</table>
<h2>Checklist por ambiente</h2>
{checklist}
<h2>Diário e documentação As-Built</h2>
{''.join(blocos) or '<p>Nenhum registro.</p>'}
</body></html>"""


# --------------------------------------------------------------------------- #
# Componentes visuais
# --------------------------------------------------------------------------- #
def lista_tarefas_simples(tarefas: list[dict]) -> None:
    """Blocos de tarefas por ambiente, somente leitura (resultado do processamento / cards)."""
    for ambiente, itens in agrupar_por_ambiente(tarefas).items():
        with st.container(border=True):
            st.markdown(f"**🏠 {ambiente}**")
            for t in itens:
                st.markdown(f"{'✅' if t['concluida'] else '⏳'} {t['descricao']}")


def card_registro(r: dict, tarefas: list[dict] | None = None, com_acoes: bool = False) -> None:
    """Card de um registro: data, badge, áudio, atraso, resumo, tarefas, foto + As-Built, transcrição."""
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

        if tarefas:
            st.markdown("**🧱 Tarefas identificadas:**")
            lista_tarefas_simples(tarefas)

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

        if com_acoes:
            with st.container(horizontal=True):
                if st.button("✏️ Editar Registro", key=f"edit_reg_{r['id']}", help="Corrigir transcrição, resumo, produtividade, atrasos ou As-Built."):
                    dialog_editar_registro(r)
                if st.button("🗑️ Excluir Registro", key=f"del_reg_{r['id']}", help="Remove o registro, o áudio e a foto (pede confirmação)."):
                    dialog_excluir_registro(r, len(tarefas or []))


# --------------------------------------------------------------------------- #
# Modais (st.dialog)
# --------------------------------------------------------------------------- #
@st.dialog("✏️ Editar Registro", width="large")
def dialog_editar_registro(r: dict) -> None:
    with st.form(f"form_dialog_reg_{r['id']}", enter_to_submit=False, border=False):
        c1, c2 = st.columns(2)
        data_reg = c1.date_input(
            "Data do registro", value=para_data(r["data_registro"]), format="DD/MM/YYYY",
            help="Dia a que o relato se refere.",
        )
        nivel_atual = r.get("nivel_produtividade")
        nivel = c2.selectbox(
            "Produtividade", NIVEIS, index=NIVEIS.index(nivel_atual) if nivel_atual in NIVEIS else 1,
            help="Entregas do dia frente aos imprevistos.",
        )
        transcricao = st.text_area("Transcrição do áudio", value=r.get("transcricao_audio") or "", height=140,
                                   help="Texto do relato falado. Corrija palavras que tenham sido mal entendidas.")
        resumo = st.text_area("Resumo executivo", value=r.get("resumo_executivo") or "",
                              help="Serviços do dia como serão apresentados ao cliente.")
        motivo = st.text_area("Justificativa de atraso", value=r.get("motivo_atraso") or SEM_ATRASO,
                              help=f"Chuva, faltas, material ou problemas técnicos. Sem atraso: '{SEM_ATRASO}'.")
        asbuilt = st.text_area("Mapeamento As-Built", value=r.get("mapeamento_asbuilt") or "",
                               help="Posição de canos, conduítes e fiação para manutenções futuras.")
        if st.form_submit_button("💾 Salvar alterações", type="primary", width="stretch"):
            db.atualizar_registro_diario(
                r["id"], data_reg.isoformat(), transcricao.strip(), resumo.strip(),
                nivel, motivo.strip() or SEM_ATRASO, asbuilt.strip(),
            )
            flash("Registro atualizado.")
            st.rerun()


@st.dialog("🗑️ Excluir Registro")
def dialog_excluir_registro(r: dict, qtd_tarefas: int) -> None:
    st.warning(
        f"Excluir o registro de **{formatar_data(r['data_registro'])}**? "
        "O áudio e a foto deste registro também serão apagados. Esta ação não pode ser desfeita.",
        icon="⚠️",
    )
    apagar_tarefas = False
    if qtd_tarefas:
        apagar_tarefas = st.checkbox(
            f"Excluir também as {qtd_tarefas} tarefa(s) do checklist extraídas deste registro",
            help="Desmarcado: as tarefas continuam no checklist da obra.",
        )
    c1, c2 = st.columns(2)
    if c1.button("Cancelar", width="stretch"):
        st.rerun()
    if c2.button("Excluir definitivamente", type="primary", width="stretch"):
        db.excluir_registro_diario(r["id"], excluir_tarefas=apagar_tarefas)
        flash("Registro excluído.")
        st.rerun()


@st.dialog("🗑️ Excluir Bloco da Obra")
def dialog_excluir_obra(obra: dict, qtd_registros: int, qtd_tarefas: int) -> None:
    st.error(
        f"Excluir **{obra['nome_cliente']}**? Serão apagados {qtd_registros} registro(s), "
        f"{qtd_tarefas} tarefa(s) e todos os áudios e fotos desta obra. Esta ação não pode ser desfeita.",
        icon="⚠️",
    )
    confirmado = st.checkbox("Entendo que os dados desta obra serão perdidos")
    c1, c2 = st.columns(2)
    if c1.button("Cancelar", width="stretch"):
        st.rerun()
    if c2.button("Excluir bloco", type="primary", width="stretch", disabled=not confirmado):
        db.excluir_obra(obra["id"])
        st.session_state.pop("obra_id", None)
        flash(f"Bloco “{obra['nome_cliente']}” excluído.")
        st.rerun()


# --------------------------------------------------------------------------- #
# Callbacks do checklist (rodam antes do script, atualizando o banco na hora)
# --------------------------------------------------------------------------- #
def _ao_marcar_tarefa(tarefa_id: int) -> None:
    db.alternar_status_tarefa(tarefa_id, st.session_state[f"tarefa_{tarefa_id}"])


def _ao_excluir_tarefa(tarefa_id: int, descricao: str) -> None:
    db.excluir_tarefa(tarefa_id)
    st.session_state.pop(f"tarefa_{tarefa_id}", None)
    flash(f"Tarefa “{descricao}” excluída.")


# --------------------------------------------------------------------------- #
# Sidebar: cadastro de Bloco de Cliente/Obra
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
            help="Serviços, metragens e materiais contratados. Usado para avaliar a produtividade do dia.",
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
            flash(f"Bloco “{nome.strip()}” cadastrado.")
            st.rerun()

    st.divider()
    st.caption("ObraTrack AI · Diário de obra inteligente para reformas")


# --------------------------------------------------------------------------- #
# Tela principal: seleção de Blocos
# --------------------------------------------------------------------------- #
st.title("🏗️ ObraTrack AI")

if mensagem := st.session_state.pop("flash", None):
    st.toast(mensagem, icon="✅")

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
c_titulo, c_gerenciar = st.columns([4, 1], vertical_alignment="center")
c_titulo.header(obra["nome_cliente"])
with c_gerenciar.popover("⚙️", help="Editar ou excluir este bloco"):
    with st.form(f"form_edit_obra_{obra_id}", enter_to_submit=False, border=False):
        st.markdown("**Editar bloco**")
        e_nome = st.text_input("Nome da Cliente / Obra *", value=obra["nome_cliente"],
                               help="Nome exibido no card do bloco.")
        e_end = st.text_input("Endereço", value=obra.get("endereco") or "", help="Endereço da obra.")
        e_escopo = st.text_area("Escopo da Obra", value=obra.get("escopo_resumo") or "",
                                help="Serviços contratados, usados na avaliação de produtividade.")
        e_status = st.selectbox("Status", db.STATUS_OBRA, index=db.STATUS_OBRA.index(obra["status"]),
                                help="Situação atual da obra.")
        e_inicio = st.date_input("Data de início", value=para_data(obra["data_inicio"]), format="DD/MM/YYYY",
                                 help="Data em que a obra começou.")
        if st.form_submit_button("💾 Salvar bloco", type="primary", width="stretch"):
            if e_nome.strip():
                db.editar_obra(obra_id, e_nome, e_end.strip(), e_escopo.strip(), e_status, e_inicio.isoformat())
                flash("Bloco atualizado.")
                st.rerun()
            else:
                st.error("Informe o nome da cliente/obra.")
    excluir_bloco = st.button("🗑️ Excluir bloco", width="stretch", help="Apaga a obra e todos os seus dados.")

st.markdown(
    f"📍 {obra.get('endereco') or 'Endereço não informado'}  \n"
    f"{ICONE_STATUS.get(obra['status'], '')} **{obra['status']}** · Início em {formatar_data(obra['data_inicio'])}"
)
if obra.get("escopo_resumo"):
    st.caption(f"Escopo: {obra['escopo_resumo']}")

# As métricas são desenhadas no fim do script, para já refletirem um registro recém-salvo.
area_metricas = st.container()

aba_registro, aba_checklist, aba_historico = st.tabs(
    ["🎙️ Novo Registro (Áudio/Foto)", "✅ Checklist de Tarefas (Por Ambiente)", "📋 Histórico e Diários (Editar/Excluir)"]
)


# --------------------------------------------------------------------------- #
# Aba 1: Novo Registro
# --------------------------------------------------------------------------- #
with aba_registro:
    # enter_to_submit=False: Enter num campo não dispara o processamento sem querer.
    with st.form(f"form_registro_{obra_id}", clear_on_submit=True, enter_to_submit=False):
        audio = st.audio_input(
            "Gravar relato do dia no canteiro",
            help="Toque no microfone e conte, cômodo por cômodo, o que foi feito, o que falta, o que "
            "atrasou (chuva, faltas, material) e onde passaram canos/fios.",
        )
        foto_up = st.file_uploader(
            "Adicionar foto da reforma / tubulação",
            type=["jpg", "jpeg", "png"],
            help="Fotografe a parede/piso ANTES do reboco ou revestimento, com uma referência de medida, "
            "para documentar a posição das tubulações.",
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

            with st.spinner("Transcrevendo o áudio e identificando as tarefas por ambiente..."):
                try:
                    analise = processar_registro_obra(
                        caminho_audio=audio_abs,
                        caminho_foto=foto_abs,
                        observacoes_texto=obs,
                        contexto_obra=contexto_da_obra(obra),
                    )
                except Exception:  # salvaguarda final: o registro nunca deixa de ser salvo
                    logging.getLogger("obratrack.app").exception("Erro inesperado na análise")
                    analise = gerar_analise_offline(obs, contexto_da_obra(obra), audio_abs is not None, foto_abs)

            dados = analise.model_dump(exclude={"tarefas_extraidas"})
            novo_id = db.inserir_registro(
                obra_id=obra_id,
                data_registro=data_reg.isoformat(),
                caminho_foto=caminho_foto,
                caminho_audio=caminho_audio,
                **dados,
            )
            tarefas_novas = []
            for t in analise.tarefas_extraidas:
                if t.descricao.strip():
                    tid = db.inserir_tarefa(obra_id, novo_id, t.ambiente, t.descricao, int(t.concluida))
                    tarefas_novas.append({"id": tid, "ambiente": t.ambiente.strip() or "Geral",
                                          "descricao": t.descricao.strip(), "concluida": int(t.concluida)})

            st.success("Registro salvo com sucesso no bloco da cliente!")
            if analise.modo_offline:
                st.info(analise.aviso, icon="ℹ️")
            if tarefas_novas:
                st.caption(f"🧱 {len(tarefas_novas)} tarefa(s) adicionada(s) ao checklist da obra.")
            card_registro(
                {
                    "id": novo_id,
                    "data_registro": data_reg.isoformat(),
                    "caminho_foto": str(caminho_foto) if caminho_foto else None,
                    "caminho_audio": str(caminho_audio) if caminho_audio else None,
                    **dados,
                },
                tarefas=tarefas_novas,
            )

registros = db.listar_registros_por_obra(obra_id)
tarefas = db.listar_tarefas_obra(obra_id)

if excluir_bloco:
    dialog_excluir_obra(obra, len(registros), len(tarefas))


# --------------------------------------------------------------------------- #
# Aba 2: Checklist de Tarefas por Ambiente
# --------------------------------------------------------------------------- #
with aba_checklist:
    with st.form(f"form_nova_tarefa_{obra_id}", clear_on_submit=True, enter_to_submit=False):
        st.markdown("**➕ Adicionar tarefa**")
        c_amb, c_desc = st.columns([1, 2])
        ambientes_existentes = list(dict.fromkeys(t["ambiente"] for t in tarefas))
        opcoes_ambiente = ambientes_existentes + [a for a in AMBIENTES_SUGERIDOS if a not in ambientes_existentes]
        nova_amb = c_amb.selectbox(
            "Ambiente", opcoes_ambiente, index=None, placeholder="Escolha ou digite",
            accept_new_options=True,
            help="Cômodo ou área da obra. Escolha da lista ou digite um novo nome.",
        )
        nova_desc = c_desc.text_input(
            "Descrição", placeholder="Ex: Assentar porcelanato",
            help="Atividade curta e objetiva.",
        )
        nova_ok = st.checkbox("Já concluída", help="Marque se a tarefa já foi executada.")
        if st.form_submit_button("Adicionar ao checklist", width="stretch"):
            if nova_desc.strip():
                db.inserir_tarefa(obra_id, None, nova_amb or "Geral", nova_desc, int(nova_ok))
                flash("Tarefa adicionada ao checklist.")
                st.rerun()
            else:
                st.error("Descreva a tarefa.")

    if not tarefas:
        st.info("Nenhuma tarefa ainda. Grave um relato na aba “Novo Registro” ou adicione manualmente acima.")
    else:
        concluidas = sum(t["concluida"] for t in tarefas)
        pct = concluidas / len(tarefas)
        st.progress(pct, text=f"**{pct:.0%} concluído** · {concluidas} de {len(tarefas)} tarefas")

        for ambiente, itens in agrupar_por_ambiente(tarefas).items():
            feitas = sum(t["concluida"] for t in itens)
            with st.container(border=True):
                st.markdown(f"#### 🏠 {ambiente}  \n<small>{feitas}/{len(itens)} concluídas</small>", unsafe_allow_html=True)
                for t in itens:
                    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                        st.checkbox(
                            t["descricao"],
                            value=bool(t["concluida"]),
                            key=f"tarefa_{t['id']}",
                            on_change=_ao_marcar_tarefa,
                            args=(t["id"],),
                            width="stretch",
                        )
                        with st.popover("✏️ Editar", help="Alterar texto ou ambiente da tarefa"):
                            with st.form(f"form_edit_tarefa_{t['id']}", enter_to_submit=False, border=False):
                                e_amb = st.text_input("Ambiente", value=t["ambiente"],
                                                      help="Cômodo ou área (ex.: Cozinha).")
                                e_desc = st.text_input("Descrição", value=t["descricao"],
                                                       help="Texto da tarefa.")
                                if st.form_submit_button("💾 Salvar", type="primary", width="stretch"):
                                    if e_desc.strip():
                                        db.editar_tarefa(t["id"], e_amb, e_desc)
                                        flash("Tarefa atualizada.")
                                        st.rerun()
                                    else:
                                        st.error("A descrição não pode ficar vazia.")
                        st.button(
                            "🗑️ Excluir",
                            key=f"del_tarefa_{t['id']}",
                            on_click=_ao_excluir_tarefa,
                            args=(t["id"], t["descricao"]),
                            help="Remover esta tarefa do checklist",
                        )


# --------------------------------------------------------------------------- #
# Aba 3: Histórico e Diários
# --------------------------------------------------------------------------- #
with aba_historico:
    if not registros:
        st.info("Nenhum registro ainda. Use a aba “Novo Registro” para criar o primeiro.")
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
                data=gerar_relatorio_html(obra, registros, tarefas),
                file_name=f"relatorio_obra_{obra_id}_{date.today():%Y%m%d}.html",
                mime="text/html",
                width="stretch",
                on_click="ignore",
                help="Arquivo único com fotos e checklist. Abra no navegador e use Ctrl+P para salvar em PDF.",
            )

        tarefas_por_registro: dict[int, list[dict]] = {}
        for t in tarefas:
            if t["registro_id"]:
                tarefas_por_registro.setdefault(t["registro_id"], []).append(t)

        for r in registros:  # já vem do mais recente para o mais antigo
            card_registro(r, tarefas=tarefas_por_registro.get(r["id"]), com_acoes=True)


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
