"""ObraTrack AI - interface Streamlit mobile-first."""

from __future__ import annotations

import base64
import io
import logging
import re
import unicodedata
import zipfile
from collections import Counter
from datetime import date, datetime
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
TODOS_AMBIENTES = "Todos os ambientes"
CHAVE_OBRA = "obra_selecionada_id"

# Limites de tamanho dos campos digitados (também aplicados no servidor via sanitize_text).
LIM_NOME, LIM_ENDERECO, LIM_ESCOPO = 120, 200, 2_000
LIM_OBS, LIM_AMBIENTE, LIM_TAREFA, LIM_BUSCA = 500, 60, 300, 120

st.set_page_config(
    page_title="ObraTrack AI",
    page_icon="🏗️",
    layout="centered",  # coluna única: lê melhor no celular do canteiro
    initial_sidebar_state="auto",
)

# CSS estático (nenhum dado de usuário é interpolado aqui). As cores usam transparência e
# `color: inherit` para funcionar tanto no tema escuro (padrão) quanto no claro.
st.markdown(
    """
<style>
header .stAppDeployButton, [data-testid="stAppDeployButton"] {display: none;}
footer {visibility: hidden;}

/* ---------- Cards dos blocos ---------- */
[class*="st-key-obra_card"] {
  background: rgba(148, 163, 184, .08); border-radius: 14px !important;
  border-left: 6px solid rgba(148, 163, 184, .55) !important;
}
.ot-card-titulo {font-weight: 700; font-size: 1.05rem; line-height: 1.3;}
.ot-card-sub {opacity: .72; font-size: .85rem; margin: .15rem 0 .5rem;}
.ot-barra {height: 8px; background: rgba(148, 163, 184, .25); border-radius: 999px; overflow: hidden;}
.ot-barra > div {height: 100%; background: linear-gradient(90deg, #3b82f6, #22c55e); border-radius: 999px;}
.ot-card-meta {opacity: .75; font-size: .8rem; margin-top: .35rem;}
.ot-muted {opacity: .72; font-size: .85rem;}

/* ---------- KPIs ---------- */
[data-testid="stMetric"] {background: rgba(148, 163, 184, .08); border-radius: 14px; padding: .75rem 1rem;}
[data-testid="stMetricValue"] {font-weight: 700;}

/* ---------- Badges (texto herda a cor do tema; o fundo dá o significado) ---------- */
.ot-badge {
  display: inline-block; padding: .22rem .75rem; margin: .12rem .3rem .12rem 0;
  border-radius: 999px; font-size: .8rem; font-weight: 600; line-height: 1.4; white-space: nowrap;
  color: inherit; background: rgba(148, 163, 184, .18); border: 1px solid rgba(148, 163, 184, .45);
}
.ot-badge-amb {background: rgba(14, 165, 233, .18); border-color: rgba(14, 165, 233, .5);}
.ot-badge-alta, .ot-badge-concluida {background: rgba(34, 197, 94, .18); border-color: rgba(34, 197, 94, .55);}
.ot-badge-media, .ot-badge-pausada {background: rgba(245, 158, 11, .2); border-color: rgba(245, 158, 11, .55);}
.ot-badge-baixa {background: rgba(239, 68, 68, .18); border-color: rgba(239, 68, 68, .55);}
.ot-badge-andamento {background: rgba(59, 130, 246, .2); border-color: rgba(59, 130, 246, .55);}
.ot-badge-completo {background: #16a34a; border-color: #16a34a; color: #ffffff;}
.ot-tarefas {margin: .2rem 0 .6rem; padding-left: 0; list-style: none;}
.ot-tarefas li {margin: .2rem 0;}
.ot-data {font-weight: 700;}

/* ---------- Botão "Criar Novo Bloco" em destaque na barra lateral ---------- */
.st-key-btn_novo_bloco button {
  border: 2px dashed #22c55e !important; background: rgba(34, 197, 94, .14) !important; font-weight: 700;
}

/* ---------- Área de toque confortável (canteiro / luva / tela suja) ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button,
[data-testid="stPopover"] > div > button, [data-testid="stPopoverButton"] {
  min-height: 3rem; border-radius: 10px; font-weight: 600; padding: .5rem 1rem;
}
[data-testid="stSidebar"] .stButton > button {justify-content: flex-start; text-align: left;}
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
st.session_state.setdefault(CHAVE_OBRA, None)  # estado inicial: nenhum bloco aberto


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


def formatar_bytes(n: int | None) -> str:
    if not n:
        return "-"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB".replace(".", ",")


def slug(texto: str) -> str:
    """'Banheiro Social' -> 'Banheiro-Social' (seguro para nome de arquivo)."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    )
    return re.sub(r"[^A-Za-z0-9]+", "-", sem_acento).strip("-")[:40] or "Geral"


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


def tarefas_por_registro(tarefas: list[dict]) -> dict[int, list[dict]]:
    mapa: dict[int, list[dict]] = {}
    for t in tarefas:
        if t["registro_id"]:
            mapa.setdefault(t["registro_id"], []).append(t)
    return mapa


def ambientes_do_registro(r: dict, mapa_tarefas: dict[int, list[dict]], conhecidos: list[str]) -> list[str]:
    """Ambientes de um registro: os das tarefas extraídas dele ou, sem tarefas, os citados no texto."""
    das_tarefas = list(dict.fromkeys(t["ambiente"] for t in mapa_tarefas.get(r["id"], [])))
    if das_tarefas:
        return das_tarefas
    texto = db.normalizar_busca(f"{r.get('mapeamento_asbuilt') or ''} {r.get('resumo_executivo') or ''}")
    citados = [a for a in conhecidos if db.normalizar_busca(a) in texto]
    return citados or ["Geral"]


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


def info_tamanho(r: dict, foto: Path) -> str:
    """'📦 168 KB · 97% menor que o original (5,2 MB)'."""
    atual = foto.stat().st_size
    original = r.get("foto_bytes_original")
    if original and original > atual:
        return f"📦 {formatar_bytes(atual)} · {1 - atual / original:.0%} menor que o original ({formatar_bytes(original)})"
    return f"📦 {formatar_bytes(atual)}"


def selecionar_obra(obra_id: int | None) -> None:
    st.session_state[CHAVE_OBRA] = obra_id


# --------------------------------------------------------------------------- #
# Relatórios para o cliente e a arquiteta
# --------------------------------------------------------------------------- #
def gerar_resumo_whatsapp(obra: dict, registros: list[dict], tarefas: list[dict]) -> str:
    """Texto pronto para colar no WhatsApp (*negrito* e _itálico_ do próprio WhatsApp)."""
    s = sanitize_text
    k = indicadores(registros, tarefas)
    linhas = [f"*🏗️ Relatório de Obra — {s(obra['nome_cliente'], LIM_NOME)}*"]
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
 body{{font-family:"Segoe UI",Arial,sans-serif;max-width:900px;margin:24px auto;padding:0 18px;color:#1e293b;background:#fff;line-height:1.45}}
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


def _bytes_foto_para_zip(foto: Path) -> bytes:
    """Bytes da foto para o ZIP. Fotos antigas que ainda tenham metadados são limpas antes."""
    dados = foto.read_bytes()
    try:
        with Image.open(io.BytesIO(dados)) as img:
            tem_metadados = bool(img.getexif()) or any(k in img.info for k in ("exif", "xmp", "icc_profile"))
    except Exception:
        return dados
    return security.strip_image_exif(dados) if tem_metadados else dados


def gerar_zip_asbuilt(obra: dict, registros: list[dict], tarefas: list[dict]) -> bytes:
    """ZIP em memória com as fotos renomeadas (01_2026-11-12_Banheiro-Social.jpg) e o memorial.

    Os nomes organizados existem só dentro do ZIP; no servidor as fotos seguem com nome UUID.
    """
    s = sanitize_text
    mapa = tarefas_por_registro(tarefas)
    conhecidos = db.listar_ambientes_recomendados(obra["id"])
    fotos = sorted(registros_com_foto(registros), key=lambda x: (x[0]["data_registro"], x[0]["id"]))

    sep = "=" * 72
    memorial = [
        "MEMORIAL DESCRITIVO AS-BUILT",
        sep,
        f"Obra/Cliente : {s(obra['nome_cliente'], LIM_NOME)}",
        f"Endereço     : {s(obra.get('endereco') or 'não informado', LIM_ENDERECO)}",
        f"Escopo       : {s(obra.get('escopo_resumo') or 'não informado', LIM_ESCOPO)}",
        f"Status       : {obra['status']} · Início em {formatar_data(obra['data_inicio'])}",
        f"Emitido em   : {datetime.now():%d/%m/%Y %H:%M}",
        f"Fotos        : {len(fotos)}",
        "",
        "Cada foto registra a posição real de tubulações, conduítes e fiação antes do",
        "fechamento (reboco/revestimento). Consulte antes de furar paredes ou pisos.",
        "",
        "1. FOTOS E DESCRIÇÃO TÉCNICA",
        sep,
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i, (r, foto) in enumerate(fotos, start=1):
            ambientes = ambientes_do_registro(r, mapa, conhecidos)
            nome = f"{i:02d}_{r['data_registro']}_{slug(ambientes[0])}{foto.suffix.lower()}"
            zf.writestr(nome, _bytes_foto_para_zip(foto), compress_type=zipfile.ZIP_STORED)
            memorial += [
                f"[{i:02d}] {nome}",
                f"     Data       : {formatar_data(r['data_registro'])}",
                f"     Ambiente(s): {', '.join(s(a, LIM_AMBIENTE) for a in ambientes)}",
                f"     As-Built   : {s(r.get('mapeamento_asbuilt') or '-')}",
                f"     Serviços   : {s(r.get('resumo_executivo') or '-')}",
            ]
            if tem_atraso(r.get("motivo_atraso")):
                memorial.append(f"     Atraso     : {s(r['motivo_atraso'])}")
            memorial.append("")
        if not fotos:
            memorial += ["Nenhuma foto técnica registrada.", ""]

        memorial += ["2. HISTÓRICO DA OBRA", sep]
        for r in sorted(registros, key=lambda r: (r["data_registro"], r["id"])):
            linha = f"{formatar_data(r['data_registro'])} [{r.get('nivel_produtividade') or '-'}] {s(r.get('resumo_executivo') or '-')}"
            memorial.append(linha)
            if tem_atraso(r.get("motivo_atraso")):
                memorial.append(f"    Atraso: {s(r['motivo_atraso'])}")
        if not registros:
            memorial.append("Nenhum registro.")

        memorial += ["", "3. CHECKLIST POR AMBIENTE", sep]
        for ambiente, itens in agrupar_por_ambiente(tarefas).items():
            feitas = sum(t["concluida"] for t in itens)
            memorial.append(f"{s(ambiente, LIM_AMBIENTE)} ({feitas}/{len(itens)})")
            memorial += [f"  [{'x' if t['concluida'] else ' '}] {s(t['descricao'], LIM_TAREFA)}" for t in itens]
        if not tarefas:
            memorial.append("Nenhuma tarefa registrada.")
        memorial += ["", "Gerado pelo ObraTrack AI."]

        # utf-8-sig + CRLF: acentos e quebras de linha corretos no Bloco de Notas do Windows.
        zf.writestr(
            "Memorial_Descritivo_AsBuilt.txt",
            "\r\n".join(memorial).encode("utf-8-sig"),
            compress_type=zipfile.ZIP_DEFLATED,
        )
    return buf.getvalue()


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


def card_obra(o: dict, resumo: dict, selecionada: bool, chave: str) -> None:
    """Card executivo de um bloco (usado nos atalhos da tela de boas-vindas)."""
    pct = resumo["concluidas"] / resumo["tarefas"] if resumo["tarefas"] else 0.0
    with st.container(border=True, key=f"obra_card_{chave}_{o['id']}"):
        st.markdown(
            f"<div class='ot-card-titulo'>{esc(o['nome_cliente'], LIM_NOME)}</div>"
            f"<div class='ot-card-sub'>📍 {esc(o.get('endereco') or 'Endereço não informado', LIM_ENDERECO)}</div>"
            f"{badge_status(o['status'])}"
            f"<div class='ot-barra' style='margin-top:.5rem'><div style='width:{pct * 100:.0f}%'></div></div>"
            f"<div class='ot-card-meta'>{pct:.0%} concluído · {resumo['concluidas']}/{resumo['tarefas']} tarefas · "
            f"{resumo['registros']} registro(s)</div>",
            unsafe_allow_html=True,
        )
        st.button("Abrir bloco", key=f"abrir_{chave}_{o['id']}", width="stretch",
                  type="primary" if selecionada else "secondary",
                  on_click=selecionar_obra, args=(o["id"],))


def seletor_ambiente(rotulo: str, opcoes: list[str], atual: str | None = None, **kwargs) -> str | None:
    """Selectbox com os ambientes recomendados e opção de digitar um novo."""
    if atual and atual not in opcoes:
        opcoes = [atual, *opcoes]
    return st.selectbox(
        rotulo, opcoes, index=opcoes.index(atual) if atual else None,
        placeholder="Escolha ou + digite novo ambiente...", accept_new_options=True,
        help="Sugestões aprendidas com as tarefas desta e das outras obras. Para um cômodo novo, "
        "digite o nome e confirme.",
        **kwargs,
    )


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
        selecionar_obra(None)
        flash(f"Bloco “{obra['nome_cliente']}” excluído.")
        st.rerun()


@st.dialog("📐 Foto As-Built", width="large")
def dialog_foto(r: dict, foto: Path, ambientes: list[str]) -> None:
    st.image(str(foto), width="stretch")
    st.markdown(
        f"<span class='ot-data'>📅 {esc(formatar_data(r['data_registro']))}</span> &nbsp; "
        + "".join(badge_ambiente(a) for a in ambientes),
        unsafe_allow_html=True,
    )
    st.caption(info_tamanho(r, foto))
    st.markdown(f"**📐 Descrição técnica (canos, conduítes e fiação):**\n\n{r.get('mapeamento_asbuilt') or '-'}")
    st.markdown(f"**Serviços do dia:** {r.get('resumo_executivo') or '-'}")
    if tem_atraso(r.get("motivo_atraso")):
        st.warning(f"**Justificativa de atraso:** {r['motivo_atraso']}", icon="⚠️")


# --------------------------------------------------------------------------- #
# Callbacks (rodam antes do script, atualizando o banco/estado na hora)
# --------------------------------------------------------------------------- #
def _ao_marcar_tarefa(tarefa_id: int) -> None:
    db.alternar_status_tarefa(tarefa_id, st.session_state[f"tarefa_{tarefa_id}"])


def _ao_excluir_tarefa(tarefa_id: int, descricao: str) -> None:
    db.excluir_tarefa(tarefa_id)
    st.session_state.pop(f"tarefa_{tarefa_id}", None)
    flash(f"Tarefa “{descricao}” excluída.")


def _chaves_filtro(obra_id: int) -> dict[str, str]:
    return {nome: f"f_{nome}_{obra_id}" for nome in ("busca", "ambientes", "status", "niveis", "datas", "atraso")}


def _limpar_filtros(obra_id: int) -> None:
    for chave in _chaves_filtro(obra_id).values():
        st.session_state.pop(chave, None)


# --------------------------------------------------------------------------- #
# Barra lateral: busca rápida e navegação entre os blocos
# --------------------------------------------------------------------------- #
obras = db.listar_obras()
ids_validos = {o["id"] for o in obras}
if st.session_state[CHAVE_OBRA] not in ids_validos:
    selecionar_obra(None)
obra_id = st.session_state[CHAVE_OBRA]

with st.sidebar:
    st.markdown("### 🏗️ ObraTrack AI")
    busca_cliente = st.text_input(
        "Filtrar blocos", placeholder="🔍 Filtrar cliente/endereço...", key="busca_cliente",
        max_chars=LIM_BUSCA, label_visibility="collapsed",
        help="Digite parte do nome do cliente ou do endereço.",
    )
    st.button("➕ Criar Novo Bloco de Cliente", key="btn_novo_bloco", width="stretch",
              on_click=selecionar_obra, args=(None,),
              help="Abre o cadastro de um novo bloco de cliente/obra na tela principal.")

    termo_cliente = db.normalizar_busca(sanitize_text(busca_cliente, LIM_BUSCA))
    obras_visiveis = [
        o for o in obras
        if not termo_cliente or termo_cliente in db.normalizar_busca(f"{o['nome_cliente']} {o.get('endereco') or ''}")
    ]
    st.caption(f"Blocos de Clientes/Obras ({len(obras_visiveis)} de {len(obras)})")
    for o in obras_visiveis:
        selecionada = o["id"] == obra_id
        st.button(
            f"{ICONE_STATUS.get(o['status'], '')} {o['nome_cliente']} · {o['status']}",
            key=f"sb_obra_{o['id']}",
            type="primary" if selecionada else "secondary",
            width="stretch",
            on_click=selecionar_obra, args=(o["id"],),
            help=o.get("endereco") or "Endereço não informado",
        )
    if obras and not obras_visiveis:
        st.caption("Nenhum bloco encontrado.")

    st.divider()
    if security.pin_ativo():
        st.button("🔒 Bloquear acesso", on_click=security.logout, width="stretch",
                  help="Encerra o acesso neste aparelho. Será preciso digitar o PIN novamente.")
    st.caption("Tema claro/escuro: menu ⋮ > Settings > Theme.")
    st.caption("ObraTrack AI · Diário de obra inteligente para reformas")


if mensagem := st.session_state.pop("flash", None):
    st.toast(mensagem, icon="✅")


# --------------------------------------------------------------------------- #
# Tela de boas-vindas (nenhum bloco selecionado)
# --------------------------------------------------------------------------- #
if obra_id is None:
    st.title("🏗️ ObraTrack AI")
    st.markdown("##### Diário de obra com IA: relato por voz, checklist por cômodo e documentação As-Built.")

    with st.container(border=True):
        st.subheader("➕ Cadastrar Novo Bloco de Cliente/Obra")
        with st.form("form_nova_obra", clear_on_submit=True, enter_to_submit=False, border=False):
            nome = st.text_input(
                "Nome da Cliente / Obra *", placeholder="Ex.: Reforma Cozinha - Sra. Ana", max_chars=LIM_NOME,
                help="Nome que identifica o bloco. Use o padrão 'Serviço - Cliente'.",
            )
            endereco = st.text_input(
                "Endereço", placeholder="Rua, número - bairro", max_chars=LIM_ENDERECO,
                help="Endereço da obra. Aparece no cabeçalho do bloco e no relatório do cliente.",
            )
            escopo = st.text_area(
                "Escopo da Obra", placeholder="Serviços contratados", max_chars=LIM_ESCOPO,
                help="Serviços, metragens e materiais contratados. Usado para avaliar a produtividade do dia.",
            )
            c_status, c_inicio = st.columns(2)
            status = c_status.selectbox(
                "Status", db.STATUS_OBRA, help="Situação atual da obra. Pode ser cadastrada já pausada ou concluída.",
            )
            inicio = c_inicio.date_input(
                "Data de início", value=date.today(), format="DD/MM/YYYY",
                help="Data em que a obra começou (ou começará).",
            )
            criar = st.form_submit_button("Cadastrar e abrir o bloco", type="primary", width="stretch")

        if criar:
            nome_limpo = sanitize_text(nome, LIM_NOME)
            if not nome_limpo:
                st.error("Informe o nome da cliente/obra.")
            else:
                novo_id = db.criar_obra(
                    nome_limpo, sanitize_text(endereco, LIM_ENDERECO), sanitize_text(escopo, LIM_ESCOPO),
                    status, inicio.isoformat(),
                )
                selecionar_obra(novo_id)  # abre o bloco recém-criado
                flash(f"Bloco “{nome_limpo}” cadastrado.")
                st.rerun()

    if obras_visiveis:
        st.subheader("Acesso rápido aos blocos", help="Toque em um bloco para abrir o diário daquela cliente.")
        resumos = db.resumo_obras()
        vazio = {"tarefas": 0, "concluidas": 0, "registros": 0}
        colunas = st.columns(2)  # em telas estreitas o Streamlit empilha as colunas
        for i, o in enumerate(obras_visiveis):
            with colunas[i % 2]:
                card_obra(o, resumos.get(o["id"], vazio), False, "inicio")
    elif obras:
        st.info("Nenhum bloco corresponde ao filtro da barra lateral.")
    st.stop()


# --------------------------------------------------------------------------- #
# Bloco selecionado: cabeçalho
# --------------------------------------------------------------------------- #
obra = next(o for o in obras if o["id"] == obra_id)
recomendados = db.listar_ambientes_recomendados(obra_id)

c_titulo, c_gerenciar = st.columns([5, 1], vertical_alignment="center")
c_titulo.title(obra["nome_cliente"])
with c_gerenciar.popover("⚙️", help="Editar ou excluir este bloco"):
    with st.form(f"form_edit_obra_{obra_id}", enter_to_submit=False, border=False):
        st.markdown("**Editar bloco**")
        e_nome = st.text_input("Nome da Cliente / Obra *", value=obra["nome_cliente"], max_chars=LIM_NOME,
                               help="Nome exibido no bloco.")
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


# --------------------------------------------------------------------------- #
# Busca Global e Filtros Avançados (valem para checklist, histórico e galeria)
# --------------------------------------------------------------------------- #
chaves = _chaves_filtro(obra_id)
busca_global = st.text_input(
    "Busca global na obra",
    placeholder="🔍 Pesquisar em tarefas, transcrições, canos, materiais ou atrasos...",
    key=chaves["busca"], max_chars=LIM_BUSCA,
    help="Filtra o checklist, o histórico e a galeria. Várias palavras: todas precisam aparecer. "
    "Não diferencia maiúsculas nem acentos.",
)
ambientes_da_obra = list(dict.fromkeys(t["ambiente"] for t in db.listar_tarefas_obra(obra_id)))
if chaves["ambientes"] in st.session_state:
    # Remove da seleção ambientes que deixaram de existir (ex.: última tarefa excluída).
    st.session_state[chaves["ambientes"]] = [a for a in st.session_state[chaves["ambientes"]] if a in ambientes_da_obra]

filtros_abertos = any(
    st.session_state.get(chaves[n]) for n in ("ambientes", "niveis", "datas", "atraso")
) or st.session_state.get(chaves["status"]) not in (None, "Todas")
with st.expander("⚙️ Filtros avançados", expanded=filtros_abertos):
    c1, c2 = st.columns(2)
    f_ambientes = c1.multiselect("Ambiente / Cômodo", ambientes_da_obra, key=chaves["ambientes"],
                                 placeholder="Todos", help="Checklist, registros e fotos destes cômodos.")
    f_status = c2.segmented_control("Status da tarefa", db.STATUS_TAREFA, default="Todas", key=chaves["status"],
                                    help="Vale para o checklist.") or "Todas"
    c3, c4 = st.columns(2)
    f_niveis = c3.multiselect("Produtividade", NIVEIS, key=chaves["niveis"], placeholder="Todas",
                              help="Registros (e tarefas extraídas deles) com estes níveis.")
    f_datas = c4.date_input("Intervalo de datas", value=(), format="DD/MM/YYYY", key=chaves["datas"],
                            help="Escolha a data inicial e a final. Uma só data filtra a partir dela.")
    f_atraso = st.toggle("Apenas dias com atraso/imprevisto", key=chaves["atraso"],
                         help="Somente registros com justificativa de atraso (chuva, faltas, material...).")
    st.button("Limpar filtros", on_click=_limpar_filtros, args=(obra_id,), width="stretch")

datas = tuple(f_datas) if isinstance(f_datas, (list, tuple)) else ((f_datas,) if f_datas else ())
filtro = dict(
    busca=sanitize_text(busca_global, LIM_BUSCA),
    ambientes=f_ambientes or None,
    niveis=f_niveis or None,
    data_inicio=datas[0].isoformat() if datas else None,
    data_fim=datas[1].isoformat() if len(datas) > 1 else None,
    apenas_atraso=f_atraso,
    texto_sem_atraso=SEM_ATRASO,
)
filtrando = bool(filtro["busca"] or f_ambientes or f_niveis or datas or f_atraso or f_status != "Todas")
area_resultado_busca = st.empty()

aba_registro, aba_checklist, aba_historico, aba_galeria = st.tabs([
    "🎙️ Novo Registro (Áudio/Foto)",
    "✅ Checklist de Tarefas (Por Ambiente)",
    "📋 Histórico e Diários (Editar/Excluir)",
    "📐 Galeria As-Built & Relatório",
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
            "para documentar a posição das tubulações. A foto é compactada e a localização e os dados "
            "do celular são removidos.",
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
        bytes_foto_original = None
        erros_midia: list[str] = []
        # Cada mídia é validada pelo conteúdo real e salva com nome aleatório (UUID).
        if audio:
            try:
                caminho_audio = security.validate_and_save_media(audio.getvalue(), "audio")
            except MidiaInvalidaError as exc:
                erros_midia.append(f"Áudio recusado: {exc}")
        if foto_up:
            try:
                dados_foto = foto_up.getvalue()
                caminho_foto = security.validate_and_save_media(dados_foto, "image")
                bytes_foto_original = len(dados_foto)
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
                foto_bytes_original=bytes_foto_original,
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
                    "foto_bytes_original": bytes_foto_original,
                    **dados,
                },
                tarefas=tarefas_novas,
            )

# Dados completos (KPIs, relatórios, ZIP) e dados filtrados (listas na tela).
registros = db.listar_registros_por_obra(obra_id)
tarefas = db.listar_tarefas_obra(obra_id)
registros_filtrados = db.buscar_registros(obra_id, **filtro) if filtrando else registros
tarefas_filtradas = db.buscar_tarefas(obra_id, status=f_status, **filtro) if filtrando else tarefas
mapa_tarefas = tarefas_por_registro(tarefas)

if excluir_bloco:
    dialog_excluir_obra(obra, len(registros), len(tarefas))

if filtrando:
    area_resultado_busca.caption(
        f"🔎 Filtro ativo: {len(tarefas_filtradas)} de {len(tarefas)} tarefa(s) · "
        f"{len(registros_filtrados)} de {len(registros)} registro(s)."
    )


# --------------------------------------------------------------------------- #
# Aba 2: Checklist de Tarefas por Ambiente
# --------------------------------------------------------------------------- #
with aba_checklist:
    grupos = agrupar_por_ambiente(tarefas)

    with st.expander("➕ Adicionar tarefa", expanded=not tarefas):
        with st.form(f"form_nova_tarefa_{obra_id}", clear_on_submit=True, enter_to_submit=False, border=False):
            c_amb, c_desc = st.columns([1, 2])
            with c_amb:
                nova_amb = seletor_ambiente("Ambiente", recomendados)
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

        if not tarefas_filtradas:
            st.info("Nenhuma tarefa com a busca/filtros atuais.")
        for ambiente, visiveis in agrupar_por_ambiente(tarefas_filtradas).items():
            itens = grupos.get(ambiente, visiveis)
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
                                e_amb = seletor_ambiente("Ambiente", recomendados, atual=t["ambiente"])
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


# --------------------------------------------------------------------------- #
# Aba 3: Histórico e Diários
# --------------------------------------------------------------------------- #
with aba_historico:
    if not registros:
        st.info("Nenhum registro ainda. Use a aba “Novo Registro” para criar o primeiro.")
    elif not registros_filtrados:
        st.info("Nenhum registro com a busca/filtros atuais.")
    for r in registros_filtrados:  # já vem do mais recente para o mais antigo
        card_registro(r, tarefas=mapa_tarefas.get(r["id"]), com_acoes=True)


# --------------------------------------------------------------------------- #
# Aba 4: Galeria As-Built & Relatório do Cliente
# --------------------------------------------------------------------------- #
with aba_galeria:
    st.subheader(
        "📐 Galeria As-Built",
        help="Fotos técnicas com a posição de canos, conduítes e fiação. Consulte antes de furar "
        "paredes ou pisos: funciona como manual de manutenção da obra.",
    )
    todas_fotos = registros_com_foto(registros)
    fotos = [
        (r, p, ambientes_do_registro(r, mapa_tarefas, recomendados))
        for r, p in registros_com_foto(registros_filtrados)
    ]
    if not todas_fotos:
        st.info("Nenhuma foto técnica ainda. Envie fotos na aba “Novo Registro”, de preferência antes do reboco.")
    else:
        g_amb, g_txt = st.columns([1, 2])
        opcoes_amb_galeria = [TODOS_AMBIENTES, *dict.fromkeys(a for _, _, ambs in fotos for a in ambs)]
        chave_g_amb = f"galeria_amb_{obra_id}"
        if st.session_state.get(chave_g_amb) not in opcoes_amb_galeria:
            st.session_state[chave_g_amb] = TODOS_AMBIENTES
        amb_galeria = g_amb.selectbox("Ambiente", opcoes_amb_galeria, key=chave_g_amb,
                                      help="Mostra só as fotos de um cômodo.")
        termo_galeria = g_txt.text_input(
            "Palavra-chave", placeholder="Ex.: água quente, conduíte, 30 cm", key=f"galeria_txt_{obra_id}",
            max_chars=LIM_BUSCA, help="Procura na descrição técnica, no resumo do dia e nos ambientes da foto.",
        )
        palavras = db.normalizar_busca(sanitize_text(termo_galeria, LIM_BUSCA)).split()
        fotos = [
            (r, p, ambs) for r, p, ambs in fotos
            if (amb_galeria == TODOS_AMBIENTES or amb_galeria in ambs)
            and all(
                w in db.normalizar_busca(f"{r.get('mapeamento_asbuilt') or ''} {r.get('resumo_executivo') or ''} {' '.join(ambs)}")
                for w in palavras
            )
        ]
        st.caption(f"{len(fotos)} de {len(todas_fotos)} foto(s)")
        if not fotos:
            st.info("Nenhuma foto com esses filtros.")
        grade = st.columns(2)
        for i, (r, foto, ambs) in enumerate(fotos):
            with grade[i % 2].container(border=True):
                st.image(str(foto), width="stretch")
                st.markdown(
                    f"<span class='ot-data'>📅 {esc(formatar_data(r['data_registro']))}</span><br>"
                    + "".join(badge_ambiente(a) for a in ambs),
                    unsafe_allow_html=True,
                )
                descricao = r.get("mapeamento_asbuilt") or "-"
                st.markdown(f"**Mapeamento:** {descricao[:180]}{'…' if len(descricao) > 180 else ''}")
                st.caption(info_tamanho(r, foto))
                if st.button("🔍 Ampliar", key=f"ampliar_{r['id']}", width="stretch",
                             help="Abre a foto em tamanho grande com a descrição técnica completa."):
                    dialog_foto(r, foto, ambs)

        st.download_button(
            "📦 Baixar Todas as Fotos e Memorial As-Built (.ZIP)",
            # Gerado só no clique: fotos renomeadas por data/ambiente + memorial descritivo.
            data=partial(gerar_zip_asbuilt, obra, registros, tarefas),
            file_name=f"asbuilt_obra_{obra_id}_{date.today():%Y%m%d}.zip",
            mime="application/zip",
            width="stretch",
            type="primary",
            on_click="ignore",
            help="Todas as fotos do bloco (01_2026-11-12_Banheiro-Social.jpg...) e o "
            "Memorial_Descritivo_AsBuilt.txt relacionando cada foto à descrição técnica, para a arquiteta.",
        )

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
