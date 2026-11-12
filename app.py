"""ObraTrack AI - interface Streamlit mobile-first."""

from __future__ import annotations

import base64
import io
import logging
from collections import Counter
from datetime import date
from functools import partial
from pathlib import Path

import streamlit as st
from PIL import Image

import database as db
import security
from ai_service import SEM_ATRASO, gerar_analise_offline, processar_registro_obra
from security import LIMITE_TEXTO_LONGO, MidiaInvalidaError, sanitize_text

BASE_DIR = Path(__file__).resolve().parent

NIVEIS = ("Alta", "Média", "Baixa")
CLASSE_NIVEL = {"Alta": "alta", "Média": "media", "Baixa": "baixa"}
ICONE_PRODUTIVIDADE = {"Alta": "🟢", "Média": "🟡", "Baixa": "🔴"}
ICONE_STATUS = {"Em Andamento": "🚧", "Pausada": "⏸️", "Concluída": "✅"}
CLASSE_STATUS = {"Em Andamento": "andamento", "Pausada": "pausada", "Concluída": "concluida"}
FORMATO_AUDIO = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg"}
AMBIENTES_SUGERIDOS = [
    "Geral", "Banheiro Social", "Banheiro Suíte", "Lavabo", "Cozinha", "Área de Serviço",
    "Sala", "Quarto", "Varanda", "Garagem", "Fachada", "Muro", "Calçada", "Cobertura",
]
FILTROS_STATUS = ("Todas", "Pendentes", "Concluídas")
TODOS_AMBIENTES = "Todos os ambientes"

# Limites de tamanho dos campos digitados (também aplicados no servidor via sanitize_text).
LIM_NOME, LIM_ENDERECO, LIM_ESCOPO = 120, 200, 2_000
LIM_OBS, LIM_AMBIENTE, LIM_TAREFA = 500, 60, 300

st.set_page_config(
    page_title="ObraTrack AI",
    page_icon="🏗️",
    layout="centered",  # coluna única: lê melhor no celular do canteiro
    initial_sidebar_state="auto",
)

# CSS estático (nenhum dado de usuário é interpolado aqui).
st.markdown(
    """
<style>
#MainMenu {visibility: hidden;}
header .stAppDeployButton, [data-testid="stAppDeployButton"] {display: none;}
footer {visibility: hidden;}

/* ---------- Cards dos Blocos de Clientes ---------- */
[class*="st-key-obra_card"] {
  background: #ffffff; border-radius: 14px !important;
  border-left: 6px solid #cbd5e1 !important;
  box-shadow: 0 1px 3px rgba(15, 23, 42, .08);
}
[class*="st-key-obra_card_ativo"] {
  border-left-color: #1d4ed8 !important;
  box-shadow: 0 0 0 2px rgba(29, 78, 216, .25), 0 4px 12px rgba(15, 23, 42, .10);
}
.ot-card-titulo {font-weight: 700; font-size: 1.05rem; line-height: 1.3; color: #0f172a;}
.ot-card-sub {color: #64748b; font-size: .85rem; margin: .15rem 0 .5rem;}
.ot-barra {height: 8px; background: #e2e8f0; border-radius: 999px; overflow: hidden;}
.ot-barra > div {height: 100%; background: linear-gradient(90deg, #1d4ed8, #16a34a); border-radius: 999px;}
.ot-card-meta {color: #475569; font-size: .8rem; margin-top: .35rem;}

/* ---------- KPIs ---------- */
[data-testid="stMetric"] {
  background: #ffffff; border-radius: 14px; padding: .75rem 1rem;
  box-shadow: 0 1px 3px rgba(15, 23, 42, .08);
}
[data-testid="stMetricValue"] {font-weight: 700; color: #0f172a;}

/* ---------- Badges ---------- */
.ot-badge {
  display: inline-block; padding: .22rem .75rem; margin: .12rem .3rem .12rem 0;
  border-radius: 999px; font-size: .8rem; font-weight: 600; line-height: 1.4;
  background: #f1f5f9; color: #334155; border: 1px solid #e2e8f0; white-space: nowrap;
}
.ot-badge-amb {background: #e0f2fe; color: #075985; border-color: #bae6fd;}
.ot-badge-alta {background: #dcfce7; color: #166534; border-color: #bbf7d0;}
.ot-badge-media {background: #fef3c7; color: #92400e; border-color: #fde68a;}
.ot-badge-baixa {background: #fee2e2; color: #991b1b; border-color: #fecaca;}
.ot-badge-andamento {background: #dbeafe; color: #1e40af; border-color: #bfdbfe;}
.ot-badge-pausada {background: #fef3c7; color: #92400e; border-color: #fde68a;}
.ot-badge-concluida {background: #dcfce7; color: #166534; border-color: #bbf7d0;}
.ot-badge-completo {background: #16a34a; color: #ffffff; border-color: #16a34a;}
.ot-tarefas {margin: .2rem 0 .6rem; padding-left: 0; list-style: none;}
.ot-tarefas li {margin: .2rem 0;}
.ot-data {font-weight: 700; color: #0f172a;}

/* ---------- Área de toque confortável (canteiro / luva / tela suja) ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button,
[data-testid="stPopover"] > div > button, [data-testid="stPopoverButton"] {
  min-height: 3rem; border-radius: 10px; font-weight: 600; padding: .5rem 1rem;
}
[data-testid="stCheckbox"] label {min-height: 2.75rem; align-items: center; padding: .3rem 0;}
[data-testid="stCheckbox"] label > span:first-child {transform: scale(1.3); margin-right: .7rem;}
.stTabs [data-baseweb="tab"] {min-height: 3rem; padding: 0 .9rem;}
[data-testid="stSegmentedControl"] button {min-height: 2.75rem;}
</style>
""",
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# Inicialização e acesso
# --------------------------------------------------------------------------- #
@st.cache_resource
def _inicializar() -> bool:
    # Detalhes técnicos (ex.: falhas da IA) vão para o log do servidor, nunca para a tela.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init_db()  # cria tabelas e aplica migrações
    db.seed_data()
    return True


_inicializar()
security.check_auth()  # com PIN configurado, nada abaixo é renderizado sem autenticação


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def esc(texto: object, limite: int = LIMITE_TEXTO_LONGO) -> str:
    """Texto do usuário/IA pronto para HTML customizado (unsafe_allow_html / relatório)."""
    return sanitize_text(texto, limite, escape_html=True)


def tem_atraso(motivo: str | None) -> bool:
    return bool(motivo and motivo.strip() and motivo.strip() != SEM_ATRASO)


def resolver_caminho(caminho: str | None) -> Path | None:
    """Resolve o caminho salvo no banco; só exibe arquivos existentes dentro de uploads\\."""
    p = db.caminho_midia_seguro(caminho)
    return p if p and p.is_file() else None


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


def indicadores(registros: list[dict], tarefas: list[dict]) -> dict:
    total = len(tarefas)
    concluidas = sum(t["concluida"] for t in tarefas)
    niveis = [r["nivel_produtividade"] for r in registros if r["nivel_produtividade"]]
    return {
        "tarefas": total,
        "concluidas": concluidas,
        "progresso": concluidas / total if total else 0.0,
        "dias_trabalhados": len({r["data_registro"] for r in registros}),
        "alertas_atraso": len({r["data_registro"] for r in registros if tem_atraso(r["motivo_atraso"])}),
        "registros": len(registros),
        "predominante": Counter(niveis).most_common(1)[0][0] if niveis else "—",
    }


def agrupar_por_ambiente(tarefas: list[dict]) -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = {}
    for t in tarefas:
        grupos.setdefault(t["ambiente"] or "Geral", []).append(t)
    return grupos


def registros_com_foto(registros: list[dict]) -> list[tuple[dict, Path]]:
    return [(r, p) for r in registros if (p := resolver_caminho(r.get("caminho_foto")))]


def flash(mensagem: str) -> None:
    """Mensagem exibida como toast na próxima execução (sobrevive ao st.rerun)."""
    st.session_state["flash"] = mensagem


def badge(texto: str, classe: str = "") -> str:
    """Badge arredondado em HTML. `texto` precisa já estar escapado."""
    return f"<span class='ot-badge {f'ot-badge-{classe}' if classe else ''}'>{texto}</span>"


def badge_nivel(nivel: str | None) -> str:
    nivel = nivel or "—"
    return badge(f"{ICONE_PRODUTIVIDADE.get(nivel, '')} Produtividade {esc(nivel)}", CLASSE_NIVEL.get(nivel, ""))


def badge_status(status: str) -> str:
    return badge(f"{ICONE_STATUS.get(status, '')} {esc(status)}", CLASSE_STATUS.get(status, ""))


def badge_ambiente(ambiente: str, feitas: int | None = None, total: int | None = None) -> str:
    rotulo = f"🏠 {esc(ambiente, LIM_AMBIENTE)}"
    if total is not None:
        rotulo += f": {feitas}/{total} concluídas"
    return badge(rotulo, "completo" if total and feitas == total else "amb")


def barra_texto(pct: float, segmentos: int = 10) -> str:
    cheios = round(pct * segmentos)
    return "🟩" * cheios + "⬜" * (segmentos - cheios)


# --------------------------------------------------------------------------- #
# Relatórios para o cliente
# --------------------------------------------------------------------------- #
def gerar_resumo_whatsapp(obra: dict, registros: list[dict], tarefas: list[dict]) -> str:
    """Texto pronto para colar no WhatsApp (*negrito* e _itálico_ do próprio WhatsApp)."""
    s = sanitize_text
    k = indicadores(registros, tarefas)
    linhas = [
        f"*🏗️ Relatório de Obra — {s(obra['nome_cliente'], LIM_NOME)}*",
    ]
    if obra.get("endereco"):
        linhas.append(f"📍 {s(obra['endereco'], LIM_ENDERECO)}")
    linhas += [
        f"📅 Atualizado em {date.today():%d/%m/%Y}",
        "",
        f"*Progresso:* {barra_texto(k['progresso'])} {k['progresso']:.0%}",
        f"✅ {k['concluidas']} de {k['tarefas']} tarefas concluídas · 📆 {k['dias_trabalhados']} dia(s) trabalhado(s)",
        "",
        "*Serviços concluídos por ambiente:*",
    ]
    grupos = agrupar_por_ambiente(tarefas)
    if not grupos:
        linhas.append("_Nenhuma tarefa registrada até o momento._")
    for ambiente, itens in grupos.items():
        feitas = [t for t in itens if t["concluida"]]
        linhas.append(f"🏠 *{s(ambiente, LIM_AMBIENTE)}* ({len(feitas)}/{len(itens)})")
        linhas += [f"   ✔ {s(t['descricao'], LIM_TAREFA)}" for t in feitas]
        if pendentes := len(itens) - len(feitas):
            linhas.append(f"   ⏳ {pendentes} serviço(s) em andamento")

    linhas += ["", "*Justificativas de prazo:*"]
    atrasos = sorted((r for r in registros if tem_atraso(r["motivo_atraso"])),
                     key=lambda r: (r["data_registro"], r["id"]))
    if atrasos:
        linhas += [f"• {formatar_data(r['data_registro'])}: {s(r['motivo_atraso'])}" for r in atrasos]
    else:
        linhas.append("Nenhum atraso registrado no período. ✅")
    linhas += ["", "_Relatório gerado pelo ObraTrack AI._"]
    return "\n".join(linhas)


def imagem_base64(caminho: Path, lado_max: int = 900) -> str | None:
    """Miniatura JPEG em base64, sem metadados, para embutir no relatório HTML."""
    try:
        with Image.open(caminho) as img:
            img.thumbnail((lado_max, lado_max))
            limpa = security.imagem_sem_metadados(img)
        buf = io.BytesIO()
        limpa.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def gerar_relatorio_html(obra: dict, registros: list[dict], tarefas: list[dict]) -> str:
    """Relatório autocontido (fotos embutidas, sem metadados) para enviar ou imprimir em PDF."""
    k = indicadores(registros, tarefas)
    cronologicos = sorted(registros, key=lambda r: (r["data_registro"], r["id"]))

    linhas_atraso = "".join(
        f"<tr><td class='nowrap'>{esc(formatar_data(r['data_registro']))}</td><td>{esc(r['motivo_atraso'])}</td></tr>"
        for r in cronologicos
        if tem_atraso(r["motivo_atraso"])
    ) or f"<tr><td colspan='2'>{esc(SEM_ATRASO)} no período.</td></tr>"

    checklist = "".join(
        f"<div class='amb'><h3>{esc(amb, LIM_AMBIENTE)} <span class='pill'>"
        f"{sum(t['concluida'] for t in itens)}/{len(itens)}</span></h3><ul>"
        + "".join(
            f"<li class='{'ok' if t['concluida'] else 'pend'}'>{'☑' if t['concluida'] else '☐'} {esc(t['descricao'], LIM_TAREFA)}</li>"
            for t in itens
        )
        + "</ul></div>"
        for amb, itens in agrupar_por_ambiente(tarefas).items()
    ) or "<p>Nenhuma tarefa registrada.</p>"

    galeria = []
    for r, foto in sorted(registros_com_foto(registros), key=lambda x: (x[0]["data_registro"], x[0]["id"])):
        b64 = imagem_base64(foto)
        if not b64:
            continue
        galeria.append(
            f"<figure><img src='data:image/jpeg;base64,{b64}' alt='Foto técnica'>"
            f"<figcaption><strong>{esc(formatar_data(r['data_registro']))}</strong> — "
            f"{esc(r['mapeamento_asbuilt'] or '-')}</figcaption></figure>"
        )

    diario = "".join(
        f"""<section class="dia">
  <h3>{esc(formatar_data(r['data_registro']))} <span class="pill">Produtividade {esc(r['nivel_produtividade'] or '-')}</span></h3>
  <p><strong>Serviços executados:</strong> {esc(r['resumo_executivo'] or '-')}</p>
  {f"<p class='atraso'><strong>Justificativa de atraso:</strong> {esc(r['motivo_atraso'])}</p>" if tem_atraso(r['motivo_atraso']) else ""}
</section>"""
        for r in cronologicos
    ) or "<p>Nenhum registro.</p>"

    pct = k["progresso"]
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relatório de Obra — {esc(obra['nome_cliente'], LIM_NOME)}</title>
<style>
 *{{box-sizing:border-box}}
 body{{font-family:"Segoe UI",Arial,sans-serif;max-width:900px;margin:24px auto;padding:0 18px;color:#1e293b;line-height:1.45}}
 header{{border-bottom:3px solid #1d4ed8;padding-bottom:10px;margin-bottom:18px}}
 h1{{margin:0;font-size:1.6rem}} h2{{font-size:1.15rem;margin:26px 0 10px;color:#1d4ed8}}
 h3{{font-size:1rem;margin:0 0 6px}} .meta{{color:#475569;margin:6px 0 0}}
 .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
 .kpi{{border:1px solid #e2e8f0;border-radius:12px;padding:10px 14px;background:#f8fafc}}
 .kpi span{{display:block;color:#64748b;font-size:.8rem}} .kpi strong{{font-size:1.35rem}}
 .barra{{height:12px;background:#e2e8f0;border-radius:999px;overflow:hidden;margin:12px 0 4px}}
 .barra div{{height:100%;background:#16a34a}}
 .pill{{display:inline-block;font-size:.75rem;font-weight:600;padding:2px 10px;border-radius:999px;background:#e0f2fe;color:#075985;vertical-align:middle}}
 .amb{{border:1px solid #e2e8f0;border-radius:12px;padding:10px 14px;margin-bottom:10px;page-break-inside:avoid}}
 ul{{list-style:none;padding-left:2px;margin:0}} li{{margin:3px 0}} li.pend{{color:#64748b}}
 table{{width:100%;border-collapse:collapse}} td,th{{border:1px solid #e2e8f0;padding:7px 9px;vertical-align:top;text-align:left}}
 th{{background:#f1f5f9}} .nowrap{{white-space:nowrap}}
 .galeria{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}
 figure{{margin:0;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;page-break-inside:avoid}}
 figure img{{width:100%;display:block}} figcaption{{padding:8px 10px;font-size:.88rem}}
 .dia{{border-top:1px solid #e2e8f0;padding-top:10px;margin-top:12px;page-break-inside:avoid}}
 .atraso{{background:#fff7ed;border-left:4px solid #f59e0b;padding:6px 10px;border-radius:4px}}
 footer{{margin-top:30px;color:#94a3b8;font-size:.8rem;text-align:center}}
 @media (max-width:600px){{.galeria{{grid-template-columns:1fr}}}}
 @media print{{body{{margin:0;max-width:none}} h2{{page-break-after:avoid}} a{{color:inherit}}}}
</style></head><body>
<header>
 <h1>Relatório de Obra</h1>
 <p class="meta"><strong>{esc(obra['nome_cliente'], LIM_NOME)}</strong>{f" · {esc(obra['endereco'], LIM_ENDERECO)}" if obra.get('endereco') else ""}<br>
 Escopo: {esc(obra.get('escopo_resumo') or '-', LIM_ESCOPO)}<br>
 Status: {esc(obra['status'])} · Início: {esc(formatar_data(obra['data_inicio']))} · Emitido em {date.today():%d/%m/%Y}</p>
</header>
<div class="kpis">
 <div class="kpi"><span>Progresso</span><strong>{pct:.0%}</strong></div>
 <div class="kpi"><span>Tarefas concluídas</span><strong>{k['concluidas']}/{k['tarefas']}</strong></div>
 <div class="kpi"><span>Dias trabalhados</span><strong>{k['dias_trabalhados']}</strong></div>
 <div class="kpi"><span>Dias com atraso justificado</span><strong>{k['alertas_atraso']}</strong></div>
</div>
<div class="barra"><div style="width:{pct * 100:.0f}%"></div></div>
<h2>Checklist por ambiente</h2>
{checklist}
<h2>Justificativas de prazo</h2>
<table><tr><th>Data</th><th>Motivo</th></tr>{linhas_atraso}</table>
<h2>Galeria As-Built (mapa de tubulações e instalações)</h2>
{f'<div class="galeria">{"".join(galeria)}</div>' if galeria else '<p>Nenhuma foto técnica registrada.</p>'}
<h2>Diário de obra</h2>
{diario}
<footer>Relatório gerado pelo ObraTrack AI em {date.today():%d/%m/%Y}.</footer>
</body></html>"""


# --------------------------------------------------------------------------- #
# Componentes visuais
# --------------------------------------------------------------------------- #
def lista_tarefas_simples(tarefas: list[dict]) -> None:
    """Tarefas agrupadas por ambiente, somente leitura (resultado do processamento / cards)."""
    partes = []
    for ambiente, itens in agrupar_por_ambiente(tarefas).items():
        feitas = sum(t["concluida"] for t in itens)
        itens_html = "".join(
            f"<li>{'✅' if t['concluida'] else '⏳'} {esc(t['descricao'], LIM_TAREFA)}</li>" for t in itens
        )
        partes.append(f"{badge_ambiente(ambiente, feitas, len(itens))}<ul class='ot-tarefas'>{itens_html}</ul>")
    st.markdown("".join(partes), unsafe_allow_html=True)


def tocar_audio(caminho: Path) -> None:
    st.audio(str(caminho), format=FORMATO_AUDIO.get(caminho.suffix.lower(), "audio/wav"))


def card_registro(r: dict, tarefas: list[dict] | None = None, com_acoes: bool = False) -> None:
    """Card de um registro: data, badge, áudio, atraso, resumo, tarefas, foto + As-Built, transcrição."""
    with st.container(border=True):
        st.markdown(
            f"<span class='ot-data'>📅 {esc(formatar_data(r['data_registro']))}</span> &nbsp; "
            f"{badge_nivel(r.get('nivel_produtividade'))}",
            unsafe_allow_html=True,
            help="Avaliação das entregas do dia frente aos imprevistos relatados.",
        )

        audio = resolver_caminho(r.get("caminho_audio"))
        if audio:
            st.caption("🎙️ Relato original do canteiro")
            tocar_audio(audio)

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
                                   max_chars=LIMITE_TEXTO_LONGO,
                                   help="Texto do relato falado. Corrija palavras que tenham sido mal entendidas.")
        resumo = st.text_area("Resumo executivo", value=r.get("resumo_executivo") or "", max_chars=LIMITE_TEXTO_LONGO,
                              help="Serviços do dia como serão apresentados ao cliente.")
        motivo = st.text_area("Justificativa de atraso", value=r.get("motivo_atraso") or SEM_ATRASO,
                              max_chars=LIMITE_TEXTO_LONGO,
                              help=f"Chuva, faltas, material ou problemas técnicos. Sem atraso: '{SEM_ATRASO}'.")
        asbuilt = st.text_area("Mapeamento As-Built", value=r.get("mapeamento_asbuilt") or "",
                               max_chars=LIMITE_TEXTO_LONGO,
                               help="Posição de canos, conduítes e fiação para manutenções futuras.")
        if st.form_submit_button("💾 Salvar alterações", type="primary", width="stretch"):
            longo = partial(sanitize_text, max_length=LIMITE_TEXTO_LONGO)
            db.atualizar_registro_diario(
                r["id"], data_reg.isoformat(), longo(transcricao), longo(resumo),
                nivel, longo(motivo) or SEM_ATRASO, longo(asbuilt),
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
            max_chars=LIM_NOME,
            help="Nome que identifica o bloco na tela principal. Use o padrão 'Serviço - Cliente'.",
        )
        endereco = st.text_input(
            "Endereço",
            placeholder="Rua, número - bairro",
            max_chars=LIM_ENDERECO,
            help="Endereço da obra. Aparece no cabeçalho do bloco e no relatório do cliente.",
        )
        escopo = st.text_area(
            "Escopo da Obra",
            placeholder="Serviços contratados",
            max_chars=LIM_ESCOPO,
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
        nome_limpo = sanitize_text(nome, LIM_NOME)
        if not nome_limpo:
            st.error("Informe o nome da cliente/obra.")
        else:
            novo_id = db.criar_obra(
                nome_limpo, sanitize_text(endereco, LIM_ENDERECO), sanitize_text(escopo, LIM_ESCOPO),
                status, inicio.isoformat(),
            )
            st.session_state["obra_id"] = novo_id
            flash(f"Bloco “{nome_limpo}” cadastrado.")
            st.rerun()

    st.divider()
    if security.pin_ativo():
        st.button("🔒 Bloquear acesso", on_click=security.logout, width="stretch",
                  help="Encerra o acesso neste aparelho. Será preciso digitar o PIN novamente.")
    st.caption("ObraTrack AI · Diário de obra inteligente para reformas")


# --------------------------------------------------------------------------- #
# Tela principal: Blocos de Clientes
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

resumos = db.resumo_obras()
st.subheader("Blocos de Clientes/Obras", help="Toque em um bloco para abrir o diário daquela cliente.")
colunas = st.columns(2)  # em telas estreitas o Streamlit empilha as colunas
for i, o in enumerate(obras):
    selecionada = o["id"] == st.session_state["obra_id"]
    res = resumos.get(o["id"], {"tarefas": 0, "concluidas": 0, "registros": 0})
    pct = res["concluidas"] / res["tarefas"] if res["tarefas"] else 0.0
    chave = f"obra_card_ativo_{o['id']}" if selecionada else f"obra_card_{o['id']}"
    with colunas[i % 2].container(border=True, key=chave):
        st.markdown(
            f"<div class='ot-card-titulo'>{esc(o['nome_cliente'], LIM_NOME)}</div>"
            f"<div class='ot-card-sub'>📍 {esc(o.get('endereco') or 'Endereço não informado', LIM_ENDERECO)}</div>"
            f"{badge_status(o['status'])}"
            f"<div class='ot-barra' style='margin-top:.5rem'><div style='width:{pct * 100:.0f}%'></div></div>"
            f"<div class='ot-card-meta'>{pct:.0%} concluído · {res['concluidas']}/{res['tarefas']} tarefas · "
            f"{res['registros']} registro(s)</div>",
            unsafe_allow_html=True,
        )
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
        e_nome = st.text_input("Nome da Cliente / Obra *", value=obra["nome_cliente"], max_chars=LIM_NOME,
                               help="Nome exibido no card do bloco.")
        e_end = st.text_input("Endereço", value=obra.get("endereco") or "", max_chars=LIM_ENDERECO,
                              help="Endereço da obra.")
        e_escopo = st.text_area("Escopo da Obra", value=obra.get("escopo_resumo") or "", max_chars=LIM_ESCOPO,
                                help="Serviços contratados, usados na avaliação de produtividade.")
        e_status = st.selectbox("Status", db.STATUS_OBRA, index=db.STATUS_OBRA.index(obra["status"]),
                                help="Situação atual da obra.")
        e_inicio = st.date_input("Data de início", value=para_data(obra["data_inicio"]), format="DD/MM/YYYY",
                                 help="Data em que a obra começou.")
        if st.form_submit_button("💾 Salvar bloco", type="primary", width="stretch"):
            if nome_ok := sanitize_text(e_nome, LIM_NOME):
                db.editar_obra(obra_id, nome_ok, sanitize_text(e_end, LIM_ENDERECO),
                               sanitize_text(e_escopo, LIM_ESCOPO), e_status, e_inicio.isoformat())
                flash("Bloco atualizado.")
                st.rerun()
            else:
                st.error("Informe o nome da cliente/obra.")
    excluir_bloco = st.button("🗑️ Excluir bloco", width="stretch", help="Apaga a obra e todos os seus dados.")

# Status e produtividade predominante (preenchido no fim, junto com os KPIs)
area_badges = st.empty()
st.markdown(
    f"📍 {obra.get('endereco') or 'Endereço não informado'} · Início em {formatar_data(obra['data_inicio'])}"
)
if obra.get("escopo_resumo"):
    st.caption(f"Escopo: {obra['escopo_resumo']}")

# Os KPIs são desenhados no fim do script, para já refletirem um registro recém-salvo.
area_metricas = st.container()

aba_registro, aba_checklist, aba_historico, aba_galeria = st.tabs([
    "🎙️ Novo Registro (Áudio/Foto)",
    "✅ Checklist de Tarefas (Por Ambiente)",
    "📋 Histórico e Diários (Editar/Excluir)",
    "📐 Galeria As-Built & Relatório do Cliente",
])


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
            "para documentar a posição das tubulações. A localização e os dados do celular são removidos da foto.",
        )
        c_obs, c_data = st.columns([2, 1])
        obs = c_obs.text_input(
            "Observações rápidas (opcional)",
            placeholder="Ex: faltou 1 pedreiro",
            max_chars=LIM_OBS,
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
        obs = sanitize_text(obs, LIM_OBS)
        caminho_audio = caminho_foto = None
        erros_midia: list[str] = []
        # Cada mídia é validada pelo conteúdo real e salva com nome aleatório (UUID).
        if audio:
            try:
                caminho_audio = security.validate_and_save_media(audio.getvalue(), "audio")
            except MidiaInvalidaError as exc:
                erros_midia.append(f"Áudio recusado: {exc}")
        if foto_up:
            try:
                caminho_foto = security.validate_and_save_media(foto_up.getvalue(), "image")
            except MidiaInvalidaError as exc:
                erros_midia.append(f"Foto recusada: {exc}")

        for erro in erros_midia:
            st.warning(erro, icon="⚠️")

        if not (caminho_audio or caminho_foto or obs):
            st.error("Grave um áudio, envie uma foto ou escreva uma observação.")
        else:
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

            dados = {
                campo: (valor if campo == "nivel_produtividade" else sanitize_text(valor, LIMITE_TEXTO_LONGO))
                for campo, valor in analise.model_dump(exclude={"tarefas_extraidas"}).items()
            }
            novo_id = db.inserir_registro(
                obra_id=obra_id,
                data_registro=data_reg.isoformat(),
                caminho_foto=caminho_foto,
                caminho_audio=caminho_audio,
                **dados,
            )
            tarefas_novas = []
            for t in analise.tarefas_extraidas:
                desc = sanitize_text(t.descricao, LIM_TAREFA)
                if desc:
                    amb = sanitize_text(t.ambiente, LIM_AMBIENTE) or "Geral"
                    tid = db.inserir_tarefa(obra_id, novo_id, amb, desc, int(t.concluida))
                    tarefas_novas.append({"id": tid, "ambiente": amb, "descricao": desc, "concluida": int(t.concluida)})

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
    grupos = agrupar_por_ambiente(tarefas)

    with st.expander("➕ Adicionar tarefa", expanded=not tarefas):
        with st.form(f"form_nova_tarefa_{obra_id}", clear_on_submit=True, enter_to_submit=False, border=False):
            c_amb, c_desc = st.columns([1, 2])
            ambientes_existentes = list(grupos)
            opcoes_ambiente = ambientes_existentes + [a for a in AMBIENTES_SUGERIDOS if a not in ambientes_existentes]
            nova_amb = c_amb.selectbox(
                "Ambiente", opcoes_ambiente, index=None, placeholder="Escolha ou digite",
                accept_new_options=True,
                help="Cômodo ou área da obra. Escolha da lista ou digite um novo nome.",
            )
            nova_desc = c_desc.text_input(
                "Descrição", placeholder="Ex: Assentar porcelanato", max_chars=LIM_TAREFA,
                help="Atividade curta e objetiva.",
            )
            nova_ok = st.checkbox("Já concluída", help="Marque se a tarefa já foi executada.")
            if st.form_submit_button("Adicionar ao checklist", width="stretch"):
                if desc_ok := sanitize_text(nova_desc, LIM_TAREFA):
                    db.inserir_tarefa(obra_id, None, sanitize_text(nova_amb, LIM_AMBIENTE) or "Geral", desc_ok, int(nova_ok))
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

        # Contadores por cômodo (sempre sobre todas as tarefas, independentemente dos filtros).
        st.markdown(
            "".join(badge_ambiente(amb, sum(t["concluida"] for t in itens), len(itens)) for amb, itens in grupos.items()),
            unsafe_allow_html=True,
        )

        chave_amb = f"filtro_amb_{obra_id}"
        opcoes_filtro_amb = [TODOS_AMBIENTES, *grupos]
        if st.session_state.get(chave_amb) not in opcoes_filtro_amb:
            st.session_state[chave_amb] = TODOS_AMBIENTES  # ambiente filtrado deixou de existir
        f_status, f_amb = st.columns([3, 2], vertical_alignment="bottom")
        filtro_status = f_status.segmented_control(
            "Mostrar", FILTROS_STATUS, default="Todas", key=f"filtro_status_{obra_id}",
            help="Filtra as tarefas pela situação.",
        ) or "Todas"
        filtro_amb = f_amb.selectbox("Ambiente", opcoes_filtro_amb, key=chave_amb,
                                     help="Mostra só as tarefas de um cômodo.")

        def passa_filtro(t: dict) -> bool:
            if filtro_status == "Pendentes" and t["concluida"]:
                return False
            if filtro_status == "Concluídas" and not t["concluida"]:
                return False
            return filtro_amb == TODOS_AMBIENTES or t["ambiente"] == filtro_amb

        algum_visivel = False
        for ambiente, itens in grupos.items():
            visiveis = [t for t in itens if passa_filtro(t)]
            if not visiveis:
                continue
            algum_visivel = True
            feitas = sum(t["concluida"] for t in itens)
            with st.container(border=True):
                st.markdown(badge_ambiente(ambiente, feitas, len(itens)), unsafe_allow_html=True)
                for t in visiveis:
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
                                e_amb = st.text_input("Ambiente", value=t["ambiente"], max_chars=LIM_AMBIENTE,
                                                      help="Cômodo ou área (ex.: Cozinha).")
                                e_desc = st.text_input("Descrição", value=t["descricao"], max_chars=LIM_TAREFA,
                                                       help="Texto da tarefa.")
                                if st.form_submit_button("💾 Salvar", type="primary", width="stretch"):
                                    if desc_ok := sanitize_text(e_desc, LIM_TAREFA):
                                        db.editar_tarefa(t["id"], sanitize_text(e_amb, LIM_AMBIENTE), desc_ok)
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
        if not algum_visivel:
            st.info("Nenhuma tarefa com esses filtros.")


# --------------------------------------------------------------------------- #
# Aba 3: Histórico e Diários
# --------------------------------------------------------------------------- #
with aba_historico:
    if not registros:
        st.info("Nenhum registro ainda. Use a aba “Novo Registro” para criar o primeiro.")
    else:
        tarefas_por_registro: dict[int, list[dict]] = {}
        for t in tarefas:
            if t["registro_id"]:
                tarefas_por_registro.setdefault(t["registro_id"], []).append(t)

        for r in registros:  # já vem do mais recente para o mais antigo
            card_registro(r, tarefas=tarefas_por_registro.get(r["id"]), com_acoes=True)


# --------------------------------------------------------------------------- #
# Aba 4: Galeria As-Built & Relatório do Cliente
# --------------------------------------------------------------------------- #
with aba_galeria:
    st.subheader(
        "📐 Galeria As-Built",
        help="Fotos técnicas com a posição de canos, conduítes e fiação. Consulte antes de furar "
        "paredes ou pisos: funciona como manual de manutenção da obra.",
    )
    fotos = registros_com_foto(registros)
    if not fotos:
        st.info("Nenhuma foto técnica ainda. Envie fotos na aba “Novo Registro”, de preferência antes do reboco.")
    else:
        grade = st.columns(2)
        for i, (r, foto) in enumerate(fotos):
            with grade[i % 2].container(border=True):
                st.image(str(foto), width="stretch")
                st.markdown(
                    f"<span class='ot-data'>📅 {esc(formatar_data(r['data_registro']))}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"**Mapeamento:** {r.get('mapeamento_asbuilt') or '-'}")

    st.divider()
    st.subheader(
        "📄 Relatório para o Cliente",
        help="Resumo pronto para o WhatsApp e relatório completo para enviar ou imprimir.",
    )
    st.caption("Toque no ícone de copiar, no canto do quadro, e cole na conversa com o cliente.")
    st.code(gerar_resumo_whatsapp(obra, registros, tarefas), language=None, wrap_lines=True)
    st.download_button(
        "📥 Baixar Relatório Completo (.html / Imprimível em PDF)",
        # Gerado só no clique (fotos embutidas deixam o arquivo pesado).
        data=partial(gerar_relatorio_html, obra, registros, tarefas),
        file_name=f"relatorio_obra_{obra_id}_{date.today():%Y%m%d}.html",
        mime="text/html",
        width="stretch",
        type="primary",
        on_click="ignore",
        help="Arquivo único com checklist, justificativas e galeria As-Built. Abra no navegador e use "
        "Ctrl+P (ou Compartilhar > Imprimir no celular) para salvar em PDF.",
    )


# --------------------------------------------------------------------------- #
# KPIs do bloco (desenhados por último, no espaço reservado acima das abas)
# --------------------------------------------------------------------------- #
k = indicadores(registros, tarefas)
area_badges.markdown(
    badge_status(obra["status"]) + (badge_nivel(k["predominante"]) if k["predominante"] != "—" else ""),
    unsafe_allow_html=True,
)
with area_metricas:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Progresso", f"{k['progresso']:.0%}", border=True,
              help="Percentual de tarefas do checklist já concluídas.")
    m2.metric("Total de Tarefas", k["tarefas"], border=True,
              help=f"{k['concluidas']} concluída(s) e {k['tarefas'] - k['concluidas']} pendente(s) no checklist.")
    m3.metric("Dias Trabalhados", k["dias_trabalhados"], border=True,
              help="Dias distintos com registro no diário de obra.")
    m4.metric("Alertas de Atraso", k["alertas_atraso"], border=True,
              help="Dias com chuva, falta de funcionário, material ou problema técnico documentado. "
              "Base para pedido de aditivo de prazo.")
