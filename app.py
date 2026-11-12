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
MAX_FOTOS_ENVIO = 10  # fotos por envio (registro, tarefa ou galeria)
TIPOS_FOTO = ["jpg", "jpeg", "png"]
# Ícone do bloco de ambiente (primeira palavra-chave encontrada, sem acentos).
ICONES_AMBIENTE = [
    (("banheiro", "lavabo", "suite", "wc"), "🚿"),
    (("cozinha",), "🍳"),
    (("servico", "lavanderia"), "🧺"),
    (("sala",), "🛋️"),
    (("quarto", "dormitorio"), "🛏️"),
    (("varanda", "sacada"), "🌇"),
    (("garagem",), "🚗"),
    (("hidraulica",), "🚰"),
    (("eletrica",), "⚡"),
    (("externa", "quintal", "jardim", "muro", "calcada", "fachada"), "🌳"),
    (("cobertura", "telhado", "laje"), "🏠"),
]

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
[data-testid="stMetric"] {background: rgba(148, 163, 184, .08); border-radius: 14px; padding: .75rem .8rem;}
[data-testid="stMetricValue"] {font-weight: 700;}
/* Títulos dos KPIs quebram linha em vez de virar "Total de Tar..." */
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * {
  white-space: normal !important; overflow: visible !important; text-overflow: clip !important;
  overflow-wrap: anywhere; line-height: 1.25;
}
[data-testid="stMetricLabel"] {height: auto !important; min-height: 2.5em; align-items: flex-start;}
[data-testid="stMetricLabel"] p {font-size: .85rem;}

/* ---------- Abas: quebram linha (nada cortado no celular) e Galeria em destaque ---------- */
.stTabs [data-baseweb="tab-list"] {flex-wrap: wrap; gap: .3rem; overflow: visible;}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {display: none;}
.stTabs [data-baseweb="tab"] {border-radius: 10px 10px 0 0; border-bottom: 3px solid transparent;}
.stTabs [data-baseweb="tab"][aria-selected="true"] {border-bottom-color: #3b82f6; background: rgba(59, 130, 246, .12);}
.stTabs [data-baseweb="tab"]:nth-of-type(4) {background: rgba(34, 197, 94, .14); font-weight: 700;}
.stTabs [data-baseweb="tab"]:nth-of-type(4)[aria-selected="true"] {border-bottom-color: #22c55e;}

/* ---------- Checklist: título do ambiente e seções Pendente / Feito ---------- */
.ot-amb-titulo {font-size: 1.15rem; font-weight: 700; margin: .1rem 0 .3rem;}
.ot-sec {font-weight: 700; font-size: .9rem; padding: .3rem .7rem; border-radius: 8px; margin: .5rem 0 .2rem;}
.ot-sec-pend {background: rgba(245, 158, 11, .16); border-left: 4px solid #f59e0b;}
.ot-sec-ok {background: rgba(34, 197, 94, .14); border-left: 4px solid #22c55e;}
[class*="st-key-trow_ok_"] [data-testid="stCheckbox"] p {text-decoration: line-through; opacity: .7;}
.ot-servico {font-style: italic; opacity: .9; margin: .4rem 0 .2rem;}

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


def slug(texto: str, sep: str = "-", padrao: str = "Geral") -> str:
    """'Banheiro Social' -> 'Banheiro-Social' (seguro para nome de arquivo/pasta)."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    )
    return re.sub(r"[^A-Za-z0-9]+", sep, sem_acento).strip(sep)[:40].strip(sep) or padrao


def icone_ambiente(ambiente: str) -> str:
    alvo = db.normalizar_busca(ambiente)
    return next((ic for chaves, ic in ICONES_AMBIENTE if any(c in alvo for c in chaves)), "📁")


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


def midias_com_arquivo(midias: list[dict]) -> list[tuple[dict, Path]]:
    """Fotos cujo arquivo existe em uploads\\ (ignora entradas com arquivo ausente)."""
    return [(m, p) for m in midias if (p := resolver_caminho(m.get("caminho_foto")))]


def agrupar_midias(midias: list[tuple[dict, Path]]) -> dict[str, dict[tuple[str, str], list[tuple[dict, Path]]]]:
    """{ambiente: {(titulo_servico, data): [(midia, arquivo), ...]}} — mais recentes primeiro."""
    grupos: dict[str, dict[tuple[str, str], list]] = {}
    ordenadas = sorted(midias, key=lambda x: (x[0]["data_midia"], x[0]["id"]), reverse=True)
    for m, p in ordenadas:
        servico = (m.get("titulo_servico") or "Registro fotográfico").strip()
        grupos.setdefault(m["ambiente"] or "Geral", {}).setdefault((servico, m["data_midia"]), []).append((m, p))
    return dict(sorted(grupos.items(), key=lambda kv: db.normalizar_busca(kv[0])))


def midia_corresponde(m: dict, busca: str) -> bool:
    """Busca normalizada (sem acentos/maiúsculas) em todos os textos ligados à foto."""
    return db.corresponde_busca(
        busca, m.get("ambiente"), m.get("titulo_servico"), m.get("descricao_tecnica"),
        m.get("tarefa_descricao"), m.get("transcricao_audio"), m.get("resumo_executivo"),
    )


def salvar_fotos(
    arquivos: list,
    obra_id: int,
    ambiente: str,
    titulo_servico: str = "",
    descricao_tecnica: str = "",
    tarefa_id: int | None = None,
    registro_id: int | None = None,
    data_midia: str | None = None,
) -> tuple[int, list[str]]:
    """Valida, comprime e vincula as fotos enviadas a um ambiente/serviço (e tarefa/registro).

    Retorna (quantidade salva, mensagens de erro seguras para o usuário).
    """
    salvas, erros = 0, []
    for i, arq in enumerate(arquivos[:MAX_FOTOS_ENVIO], start=1):
        dados = arq.getvalue()
        try:
            caminho = security.validate_and_save_media(dados, "image")
        except MidiaInvalidaError as exc:
            erros.append(f"Foto {i} recusada: {exc}")
            continue
        db.inserir_midia_obra(
            obra_id, caminho, sanitize_text(ambiente, LIM_AMBIENTE) or "Geral",
            sanitize_text(titulo_servico, LIM_TAREFA), sanitize_text(descricao_tecnica, LIMITE_TEXTO_LONGO),
            registro_id=registro_id, tarefa_id=tarefa_id, data_midia=data_midia, foto_bytes_original=len(dados),
        )
        salvas += 1
    if len(arquivos) > MAX_FOTOS_ENVIO:
        erros.append(f"Só as {MAX_FOTOS_ENVIO} primeiras fotos foram enviadas. Envie as demais em outro lote.")
    return salvas, erros


def vincular_fotos_do_registro(
    obra: dict,
    registro_id: int,
    data_iso: str,
    analise,
    dados: dict,
    obs: str,
    fotos_salvas: list[tuple[str, int]],
    tarefas_novas: list[dict],
) -> list[int]:
    """Grava em midias_obra as fotos de um registro, cada uma no seu ambiente e serviço.

    Usa a classificação da IA por foto; sem ela, a tarefa principal do relato (ex.: "foi
    retirado a cerâmica do banheiro" → ambiente Banheiro, serviço "Retirada da cerâmica...");
    sem tarefas, o cômodo citado no relato ou no escopo da obra.
    """
    classificadas = {f.indice_foto: f for f in analise.fotos_classificadas}
    principal = next((t for t in tarefas_novas if t["concluida"]), tarefas_novas[0] if tarefas_novas else None)
    relato = f"{analise.transcricao_audio} {obs}"
    ids = []
    for i, (caminho, tamanho) in enumerate(fotos_salvas, start=1):
        cls, tarefa = classificadas.get(i), principal
        if cls:
            amb = sanitize_text(cls.ambiente, LIM_AMBIENTE) or "Geral"
            mesmo_amb = [t for t in tarefas_novas if db.normalizar_busca(t["ambiente"]) == db.normalizar_busca(amb)]
            tarefa = next((t for t in mesmo_amb if t["concluida"]), mesmo_amb[0] if mesmo_amb else None)
            titulo, descricao = cls.titulo_servico, cls.descricao_tecnica or dados["mapeamento_asbuilt"]
        elif tarefa:
            amb, titulo, descricao = tarefa["ambiente"], tarefa["descricao"], dados["mapeamento_asbuilt"]
        else:
            escopo = obra.get("escopo_resumo") or ""
            internos = [a for a in db.ambientes_citados(escopo) if a not in db.AMBIENTES_EXTERNOS]
            amb = db.detectar_ambiente(relato, escopo, padrao=internos[0] if len(internos) == 1 else "Geral")
            titulo, descricao = "Registro fotográfico do dia", dados["mapeamento_asbuilt"]
        ids.append(db.inserir_midia_obra(
            obra["id"], caminho, amb, sanitize_text(titulo, LIM_TAREFA) or "Registro fotográfico do dia",
            sanitize_text(descricao, LIMITE_TEXTO_LONGO), registro_id=registro_id,
            tarefa_id=tarefa["id"] if tarefa else None, data_midia=data_iso, foto_bytes_original=tamanho,
        ))
    return ids


def grade_fotos(fotos: list[tuple[dict, Path]], prefixo: str, colunas: int = 3, acoes: bool = True) -> None:
    """Grade de miniaturas com ampliar / editar / excluir."""
    grade = st.columns(colunas)
    for i, (m, foto) in enumerate(fotos):
        with grade[i % colunas].container(border=True):
            st.image(str(foto), width="stretch")
            st.caption(info_tamanho(m, foto))
            if acoes:
                with st.container(horizontal=True, gap="small"):
                    if st.button("🔍", key=f"{prefixo}_amp_{m['id']}", help="Ampliar a foto com a descrição técnica"):
                        dialog_foto(m, foto)
                    if st.button("✏️", key=f"{prefixo}_edit_{m['id']}", help="Editar ambiente, serviço e descrição da foto"):
                        dialog_editar_midia(m)
                    if st.button("🗑️", key=f"{prefixo}_del_{m['id']}", help="Excluir esta foto"):
                        dialog_excluir_midia(m)


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


def gerar_relatorio_html(obra: dict, registros: list[dict], tarefas: list[dict], midias: list[dict]) -> str:
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
    for ambiente, servicos in agrupar_midias(midias_com_arquivo(midias)).items():
        figuras = []
        for (servico, data_m), itens in servicos.items():
            for m, foto in itens:
                if not (b64 := imagem_base64(foto)):
                    continue
                detalhe = f"<br>{esc(m['descricao_tecnica'])}" if m.get("descricao_tecnica") else ""
                figuras.append(
                    f"<figure><img src='data:image/jpeg;base64,{b64}' alt='Foto técnica'>"
                    f"<figcaption><strong>{esc(servico, LIM_TAREFA)}</strong> — {esc(formatar_data(data_m))}"
                    f"{detalhe}</figcaption></figure>"
                )
        if figuras:
            galeria.append(f"<h3>{icone_ambiente(ambiente)} {esc(ambiente, LIM_AMBIENTE)}</h3>"
                           f"<div class='galeria'>{''.join(figuras)}</div>")

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
 .galeria{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:16px}}
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
<h2>Galeria da obra por ambiente (As-Built)</h2>
{"".join(galeria) if galeria else '<p>Nenhuma foto técnica registrada.</p>'}
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


def gerar_zip_asbuilt(obra: dict, registros: list[dict], tarefas: list[dict], midias: list[dict]) -> bytes:
    """ZIP em memória com as fotos em subpastas por ambiente e o memorial descritivo.

    Ex.: Banheiro_Social/01_2026-11-11_retirada_da_ceramica.jpg, Cozinha/01_...jpg.
    Os nomes organizados existem só dentro do ZIP; no servidor as fotos seguem com nome UUID.
    """
    s = sanitize_text
    # Pastas por ambiente (ambientes que só diferem em acento/maiúscula caem na mesma pasta).
    pastas: dict[str, list[tuple[dict, Path]]] = {}
    for m, foto in sorted(midias_com_arquivo(midias), key=lambda x: (x[0]["data_midia"], x[0]["id"])):
        pastas.setdefault(slug(m["ambiente"], "_"), []).append((m, foto))
    pastas = dict(sorted(pastas.items(), key=lambda kv: kv[0].casefold()))
    total_fotos = sum(len(v) for v in pastas.values())

    sep = "=" * 72
    memorial = [
        "MEMORIAL DESCRITIVO AS-BUILT",
        sep,
        f"Obra/Cliente : {s(obra['nome_cliente'], LIM_NOME)}",
        f"Endereço     : {s(obra.get('endereco') or 'não informado', LIM_ENDERECO)}",
        f"Escopo       : {s(obra.get('escopo_resumo') or 'não informado', LIM_ESCOPO)}",
        f"Status       : {obra['status']} · Início em {formatar_data(obra['data_inicio'])}",
        f"Emitido em   : {datetime.now():%d/%m/%Y %H:%M}",
        f"Fotos        : {total_fotos} em {len(pastas)} pasta(s) de ambiente",
        "",
        "Cada foto registra a posição real de tubulações, conduítes e fiação antes do",
        "fechamento (reboco/revestimento). Consulte antes de furar paredes ou pisos.",
        "",
        "1. FOTOS POR AMBIENTE E SERVIÇO",
        sep,
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for pasta, itens in pastas.items():
            memorial += [f"AMBIENTE: {s(itens[0][0]['ambiente'], LIM_AMBIENTE).upper()}  (pasta {pasta}/)", "-" * 72]
            for i, (m, foto) in enumerate(itens, start=1):
                servico = m.get("titulo_servico") or "Registro fotográfico"
                nome = f"{pasta}/{i:02d}_{m['data_midia']}_{slug(servico, '_', 'foto').lower()}{foto.suffix.lower()}"
                zf.writestr(nome, _bytes_foto_para_zip(foto), compress_type=zipfile.ZIP_STORED)
                memorial += [
                    f"[{i:02d}] {nome}",
                    f"     Data       : {formatar_data(m['data_midia'])}",
                    f"     Serviço    : {s(servico, LIM_TAREFA)}",
                    f"     As-Built   : {s(m.get('descricao_tecnica') or '-')}",
                ]
                if m.get("tarefa_descricao"):
                    status = "concluída" if m.get("tarefa_concluida") else "pendente"
                    memorial.append(f"     Tarefa     : {s(m['tarefa_descricao'], LIM_TAREFA)} ({status})")
                if tem_atraso(m.get("motivo_atraso")):
                    memorial.append(f"     Atraso     : {s(m['motivo_atraso'])}")
                memorial.append("")
        if not pastas:
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


def card_registro(r: dict, tarefas: list[dict] | None = None, com_acoes: bool = False,
                  midias: list[dict] | None = None) -> None:
    """Card de um registro: data, badge, áudio, atraso, resumo, tarefas, fotos + As-Built, transcrição."""
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

        if r.get("mapeamento_asbuilt"):
            st.markdown(f"**📐 Mapeamento As-Built:** {r['mapeamento_asbuilt']}")
        fotos = midias_com_arquivo(midias or [])
        if fotos:
            st.markdown(f"**📷 {len(fotos)} foto(s) do dia:** "
                        + "".join(badge_ambiente(a) for a in dict.fromkeys(m["ambiente"] for m, _ in fotos)),
                        unsafe_allow_html=True)
            colunas = st.columns(min(len(fotos), 3))
            for i, (m, foto) in enumerate(fotos):
                colunas[i % len(colunas)].image(str(foto), width="stretch",
                                                caption=sanitize_text(m.get("titulo_servico"), LIM_TAREFA) or None)

        if r.get("transcricao_audio"):
            with st.expander("📝 Transcrição do áudio"):
                st.write(r["transcricao_audio"])

        if com_acoes:
            with st.container(horizontal=True):
                if st.button("✏️ Editar Registro", key=f"edit_reg_{r['id']}", help="Corrigir transcrição, resumo, produtividade, atrasos ou As-Built."):
                    dialog_editar_registro(r)
                if st.button("🗑️ Excluir Registro", key=f"del_reg_{r['id']}", help="Remove o registro e o áudio (pede confirmação)."):
                    dialog_excluir_registro(r, len(tarefas or []), len(fotos))


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
def dialog_excluir_registro(r: dict, qtd_tarefas: int, qtd_fotos: int = 0) -> None:
    st.warning(
        f"Excluir o registro de **{formatar_data(r['data_registro'])}**? "
        "O áudio deste registro também será apagado. Esta ação não pode ser desfeita.",
        icon="⚠️",
    )
    apagar_tarefas = apagar_fotos = False
    if qtd_tarefas:
        apagar_tarefas = st.checkbox(
            f"Excluir também as {qtd_tarefas} tarefa(s) do checklist extraídas deste registro",
            help="Desmarcado: as tarefas continuam no checklist da obra.",
        )
    if qtd_fotos:
        apagar_fotos = st.checkbox(
            f"Excluir também as {qtd_fotos} foto(s) deste registro",
            help="Desmarcado: as fotos continuam na Galeria da Obra, no ambiente delas.",
        )
    c1, c2 = st.columns(2)
    if c1.button("Cancelar", width="stretch"):
        st.rerun()
    # A exclusão roda no on_click (antes de qualquer rerun, do dialog ou da página inteira).
    if c2.button("Excluir definitivamente", type="primary", width="stretch", key=f"ok_del_reg_{r['id']}",
                 on_click=_excluir_registro_cb, args=(r["id"], apagar_tarefas, apagar_fotos)):
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
    if c2.button("Excluir bloco", type="primary", width="stretch", disabled=not confirmado,
                 key=f"ok_del_obra_{obra['id']}", on_click=_excluir_obra_cb, args=(obra["id"], obra["nome_cliente"])):
        st.rerun()


@st.dialog("📐 Foto As-Built", width="large")
def dialog_foto(m: dict, foto: Path) -> None:
    st.image(str(foto), width="stretch")
    st.markdown(
        f"<span class='ot-data'>📅 {esc(formatar_data(m['data_midia']))}</span> &nbsp; {badge_ambiente(m['ambiente'])}",
        unsafe_allow_html=True,
    )
    st.markdown(f"**Serviço:** {m.get('titulo_servico') or '-'}")
    st.caption(info_tamanho(m, foto))
    st.markdown(f"**📐 Descrição técnica (canos, conduítes e fiação):**\n\n{m.get('descricao_tecnica') or '-'}")
    if m.get("tarefa_descricao"):
        st.markdown(f"**Tarefa vinculada:** {'✅' if m.get('tarefa_concluida') else '⏳'} {m['tarefa_descricao']}")
    if m.get("resumo_executivo"):
        st.markdown(f"**Serviços do dia:** {m['resumo_executivo']}")
    if tem_atraso(m.get("motivo_atraso")):
        st.warning(f"**Justificativa de atraso:** {m['motivo_atraso']}", icon="⚠️")


@st.dialog("✏️ Editar Foto")
def dialog_editar_midia(m: dict) -> None:
    with st.form(f"form_edit_midia_{m['id']}", enter_to_submit=False, border=False):
        amb = seletor_ambiente("Ambiente", db.listar_ambientes_recomendados(m["obra_id"]), atual=m["ambiente"])
        titulo = st.text_input("Serviço realizado", value=m.get("titulo_servico") or "", max_chars=LIM_TAREFA,
                               help="Ex.: Retirada da cerâmica do banheiro.")
        descricao = st.text_area("Descrição técnica", value=m.get("descricao_tecnica") or "",
                                 max_chars=LIMITE_TEXTO_LONGO,
                                 help="Posição de canos, conduítes e fiação, medidas de referência.")
        if st.form_submit_button("💾 Salvar foto", type="primary", width="stretch"):
            db.editar_midia_obra(m["id"], sanitize_text(amb, LIM_AMBIENTE) or "Geral",
                                 sanitize_text(titulo, LIM_TAREFA), sanitize_text(descricao, LIMITE_TEXTO_LONGO))
            flash("Foto atualizada.")
            st.rerun()


@st.dialog("🗑️ Excluir Foto")
def dialog_excluir_midia(m: dict) -> None:
    if foto := resolver_caminho(m.get("caminho_foto")):
        st.image(str(foto), width=240)
    st.warning(f"Excluir esta foto de **{m['ambiente']}**? Esta ação não pode ser desfeita.", icon="⚠️")
    c1, c2 = st.columns(2)
    if c1.button("Cancelar", width="stretch", key=f"cancel_del_midia_{m['id']}"):
        st.rerun()
    if c2.button("Excluir foto", type="primary", width="stretch", key=f"ok_del_midia_{m['id']}",
                 on_click=_excluir_midia_cb, args=(m["id"],)):
        st.rerun()


def _excluir_registro_cb(registro_id: int, excluir_tarefas: bool, excluir_fotos: bool) -> None:
    db.excluir_registro_diario(registro_id, excluir_tarefas=excluir_tarefas, excluir_fotos=excluir_fotos)
    flash("Registro excluído.")


def _excluir_obra_cb(obra_id: int, nome: str) -> None:
    db.excluir_obra(obra_id)
    selecionar_obra(None)
    flash(f"Bloco “{nome}” excluído.")


def _excluir_midia_cb(midia_id: int) -> None:
    db.excluir_midia_obra(midia_id)
    flash("Foto excluída.")


# --------------------------------------------------------------------------- #
# Callbacks (rodam antes do script, atualizando o banco/estado na hora)
# --------------------------------------------------------------------------- #
def _ao_marcar_tarefa(tarefa_id: int, descricao: str) -> None:
    feita = bool(st.session_state[f"tarefa_{tarefa_id}"])
    db.alternar_status_tarefa(tarefa_id, feita)
    flash(f"“{descricao}” {'marcada como ✅ feita' if feita else 'voltou para ⏳ pendente'}.")


def _ao_excluir_tarefa(tarefa_id: int, descricao: str) -> None:
    db.excluir_tarefa(tarefa_id)
    st.session_state.pop(f"tarefa_{tarefa_id}", None)
    flash(f"Tarefa “{descricao}” excluída.")


def _chaves_filtro(obra_id: int) -> dict[str, str]:
    return {nome: f"f_{nome}_{obra_id}" for nome in ("busca", "ambientes", "status", "niveis", "datas", "atraso")}


def _limpar_filtros(obra_id: int) -> None:
    """Reseta a busca global, os Filtros avançados e os filtros da galeria."""
    for chave in [*_chaves_filtro(obra_id).values(), f"galeria_amb_{obra_id}", f"galeria_txt_{obra_id}"]:
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
c_busca, c_limpar = st.columns([4, 1], vertical_alignment="bottom")
busca_global = c_busca.text_input(
    "Busca global na obra",
    placeholder="🔍 Pesquisar em tarefas, ambientes, transcrições, serviços das fotos...",
    key=chaves["busca"], max_chars=LIM_BUSCA,
    help="Filtra o checklist, o histórico e a galeria. Procura em ambiente, tarefa, transcrição, resumo "
    "do dia e serviço das fotos. Várias palavras: todas precisam aparecer. Não diferencia maiúsculas nem acentos.",
)
c_limpar.button("✖️ Limpar Filtros", key=f"btn_limpar_topo_{obra_id}", on_click=_limpar_filtros, args=(obra_id,),
                width="stretch", help="Apaga a busca e todos os filtros avançados de uma vez.")
midias_obra = db.listar_midias_obra(obra_id)
ambientes_da_obra = list(dict.fromkeys(
    [t["ambiente"] for t in db.listar_tarefas_obra(obra_id)] + [m["ambiente"] for m in midias_obra]
))
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
    "🖼️ Galeria da Obra & Relatórios",
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
        fotos_up = st.file_uploader(
            "Adicionar fotos da reforma / tubulação (pode enviar várias)",
            type=TIPOS_FOTO,
            accept_multiple_files=True,
            help="Fotografe a parede/piso ANTES do reboco ou revestimento, com uma referência de medida. "
            "As fotos vão para a Galeria no ambiente e serviço do relato (ex.: 'retirei a cerâmica do "
            f"banheiro' → Banheiro). Até {MAX_FOTOS_ENVIO} por registro; são compactadas e a localização "
            "e os dados do celular são removidos.",
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
        caminho_audio = None
        fotos_salvas: list[tuple[str, int]] = []  # (caminho relativo, tamanho original)
        erros_midia: list[str] = []
        # Cada mídia é validada pelo conteúdo real e salva com nome aleatório (UUID).
        if audio:
            try:
                caminho_audio = security.validate_and_save_media(audio.getvalue(), "audio")
            except MidiaInvalidaError as exc:
                erros_midia.append(f"Áudio recusado: {exc}")
        for i, arq in enumerate((fotos_up or [])[:MAX_FOTOS_ENVIO], start=1):
            try:
                dados_foto = arq.getvalue()
                fotos_salvas.append((security.validate_and_save_media(dados_foto, "image"), len(dados_foto)))
            except MidiaInvalidaError as exc:
                erros_midia.append(f"Foto {i} recusada: {exc}")
        if len(fotos_up or []) > MAX_FOTOS_ENVIO:
            erros_midia.append(f"Só as {MAX_FOTOS_ENVIO} primeiras fotos foram usadas neste registro.")

        for erro in erros_midia:
            st.warning(erro, icon="⚠️")

        if not (caminho_audio or fotos_salvas or obs):
            st.error("Grave um áudio, envie uma foto ou escreva uma observação.")
        else:
            audio_abs = BASE_DIR / caminho_audio if caminho_audio else None
            fotos_abs = [BASE_DIR / c for c, _ in fotos_salvas]

            with st.spinner("Transcrevendo o áudio e identificando as tarefas e fotos por ambiente..."):
                try:
                    analise = processar_registro_obra(
                        caminho_audio=audio_abs,
                        caminho_foto=fotos_abs,
                        observacoes_texto=obs,
                        contexto_obra=contexto_da_obra(obra),
                    )
                except Exception:  # salvaguarda final: o registro nunca deixa de ser salvo
                    logging.getLogger("obratrack.app").exception("Erro inesperado na análise")
                    analise = gerar_analise_offline(obs, contexto_da_obra(obra), audio_abs is not None, fotos_abs)

            dados = {
                campo: (valor if campo == "nivel_produtividade" else sanitize_text(valor, LIMITE_TEXTO_LONGO))
                for campo, valor in analise.model_dump(exclude={"tarefas_extraidas", "fotos_classificadas"}).items()
            }
            novo_id = db.inserir_registro(
                obra_id=obra_id,
                data_registro=data_reg.isoformat(),
                caminho_audio=caminho_audio,
                **dados,  # as fotos vão para midias_obra (galeria por ambiente), abaixo
            )
            tarefas_novas = []
            for t in analise.tarefas_extraidas:
                desc = sanitize_text(t.descricao, LIM_TAREFA)
                if desc:
                    amb = sanitize_text(t.ambiente, LIM_AMBIENTE) or "Geral"
                    tid = db.inserir_tarefa(obra_id, novo_id, amb, desc, int(t.concluida))
                    tarefas_novas.append({"id": tid, "ambiente": amb, "descricao": desc, "concluida": int(t.concluida)})

            vincular_fotos_do_registro(obra, novo_id, data_reg.isoformat(), analise, dados, obs,
                                       fotos_salvas, tarefas_novas)

            st.success("Registro salvo com sucesso no bloco da cliente!")
            if analise.modo_offline:
                st.info(analise.aviso, icon="ℹ️")
            if tarefas_novas:
                st.caption(f"🧱 {len(tarefas_novas)} tarefa(s) adicionada(s) ao checklist da obra.")
            if fotos_salvas:
                st.caption(f"🖼️ {len(fotos_salvas)} foto(s) vinculada(s) na Galeria da Obra.")
            card_registro(
                {"id": novo_id, "data_registro": data_reg.isoformat(),
                 "caminho_audio": str(caminho_audio) if caminho_audio else None, **dados},
                tarefas=tarefas_novas,
                midias=db.listar_midias_obra(obra_id, registro_id=novo_id),
            )

# Dados completos (KPIs, relatórios, ZIP) e dados filtrados (listas na tela).
registros = db.listar_registros_por_obra(obra_id)
tarefas = db.listar_tarefas_obra(obra_id)
registros_filtrados = db.buscar_registros(obra_id, **filtro) if filtrando else registros
tarefas_filtradas = db.buscar_tarefas(obra_id, status=f_status, **filtro) if filtrando else tarefas
mapa_tarefas = tarefas_por_registro(tarefas)
midias_obra = db.listar_midias_obra(obra_id)  # recarrega: o registro acima pode ter adicionado fotos
midias_por_registro: dict[int, list[dict]] = {}
midias_por_tarefa: dict[int, list[dict]] = {}
for _m in midias_obra:
    if _m["registro_id"]:
        midias_por_registro.setdefault(_m["registro_id"], []).append(_m)
    if _m["tarefa_id"]:
        midias_por_tarefa.setdefault(_m["tarefa_id"], []).append(_m)


def filtrar_midias(midias: list[dict]) -> list[dict]:
    """Aplica a busca global e os filtros avançados às fotos da galeria."""
    if not filtrando:
        return midias
    ids_registros = None
    if filtro["niveis"] or filtro["apenas_atraso"]:
        # Produtividade/atraso pertencem ao registro do dia em que a foto foi enviada.
        ids_registros = {r["id"] for r in db.buscar_registros(
            obra_id, niveis=filtro["niveis"], apenas_atraso=filtro["apenas_atraso"], texto_sem_atraso=SEM_ATRASO)}
    return [
        m for m in midias
        if midia_corresponde(m, filtro["busca"])
        and (not filtro["ambientes"] or m["ambiente"] in filtro["ambientes"])
        and (not filtro["data_inicio"] or m["data_midia"] >= filtro["data_inicio"])
        and (not filtro["data_fim"] or m["data_midia"] <= filtro["data_fim"])
        and (ids_registros is None or m["registro_id"] in ids_registros)
    ]

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
def linha_tarefa(t: dict) -> None:
    """Uma tarefa: checkbox (1 clique, on_change), editar, anexar fotos e excluir."""
    chave = f"tarefa_{t['id']}"
    # O estado do checkbox segue sempre o banco (sem `value=`), então um clique basta e
    # a tarefa troca de seção (Pendente ↔ Feito) já na próxima renderização.
    st.session_state[chave] = bool(t["concluida"])
    fotos_t = midias_por_tarefa.get(t["id"], [])
    secao = "ok" if t["concluida"] else "pend"
    with st.container(horizontal=True, vertical_alignment="center", gap="small", key=f"trow_{secao}_{t['id']}"):
        st.checkbox(t["descricao"], key=chave, on_change=_ao_marcar_tarefa, args=(t["id"], t["descricao"]),
                    width="stretch", help="Marque quando o serviço estiver feito; desmarque para voltar a pendente.")
        if fotos_t:
            st.caption(f"📷 {len(fotos_t)}", help="Fotos vinculadas a esta tarefa (veja na Galeria da Obra).")
        with st.popover("✏️", help="Editar texto ou trocar o ambiente (ex.: tarefa que caiu em “Área Externa”)"):
            with st.form(f"form_edit_tarefa_{t['id']}", enter_to_submit=False, border=False):
                e_amb = seletor_ambiente("Ambiente", recomendados, atual=t["ambiente"])
                e_desc = st.text_input("Descrição", value=t["descricao"], max_chars=LIM_TAREFA, help="Texto da tarefa.")
                st.caption("As fotos desta tarefa acompanham a troca de ambiente.")
                if st.form_submit_button("💾 Salvar", type="primary", width="stretch"):
                    if desc_ok := sanitize_text(e_desc, LIM_TAREFA):
                        db.editar_tarefa(t["id"], sanitize_text(e_amb, LIM_AMBIENTE), desc_ok)
                        flash("Tarefa atualizada.")
                        st.rerun()
                    else:
                        st.error("A descrição não pode ficar vazia.")
        with st.popover("📷", help="Anexar fotos a esta tarefa"):
            with st.form(f"form_fotos_tarefa_{t['id']}", clear_on_submit=True, enter_to_submit=False, border=False):
                st.markdown(f"**Fotos de:** {t['descricao']}")
                novas = st.file_uploader("Fotos", type=TIPOS_FOTO, accept_multiple_files=True,
                                         key=f"up_tarefa_{t['id']}", help=f"Até {MAX_FOTOS_ENVIO} por envio.")
                desc_foto = st.text_input("Descrição técnica (opcional)", max_chars=LIM_TAREFA,
                                          placeholder="Ex.: cano de água fria a 30 cm do piso")
                if st.form_submit_button("📎 Anexar fotos", type="primary", width="stretch"):
                    if not novas:
                        st.error("Escolha ao menos uma foto.")
                    else:
                        n, erros = salvar_fotos(novas, obra_id, t["ambiente"], t["descricao"], desc_foto,
                                                tarefa_id=t["id"])
                        for e in erros:
                            st.warning(e, icon="⚠️")
                        if n:
                            flash(f"{n} foto(s) anexada(s) à tarefa.")
                            st.rerun()
        st.button("🗑️", key=f"del_tarefa_{t['id']}", on_click=_ao_excluir_tarefa,
                  args=(t["id"], t["descricao"]), help="Excluir esta tarefa do checklist (as fotos ficam na galeria)")


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
            novas_fotos = st.file_uploader(
                "Fotos desta tarefa (opcional)", type=TIPOS_FOTO, accept_multiple_files=True,
                key=f"up_nova_tarefa_{obra_id}",
                help="As fotos entram na Galeria da Obra já no ambiente e no serviço desta tarefa.",
            )
            if st.form_submit_button("Adicionar ao checklist", width="stretch"):
                if desc_ok := sanitize_text(nova_desc, LIM_TAREFA):
                    amb_ok = sanitize_text(nova_amb, LIM_AMBIENTE) or db.detectar_ambiente(desc_ok, obra.get("escopo_resumo") or "")
                    tid = db.inserir_tarefa(obra_id, None, amb_ok, desc_ok, int(nova_ok))
                    n, erros = salvar_fotos(novas_fotos or [], obra_id, amb_ok, desc_ok, tarefa_id=tid)
                    for e in erros:
                        st.warning(e, icon="⚠️")
                    flash(f"Tarefa adicionada em {amb_ok}" + (f" com {n} foto(s)." if n else "."))
                    if not erros:
                        st.rerun()
                else:
                    st.error("Descreva a tarefa.")

    if not tarefas:
        st.info("Nenhuma tarefa ainda. Grave um relato na aba “Novo Registro” ou adicione manualmente acima.")
    else:
        concluidas = sum(t["concluida"] for t in tarefas)
        pct = concluidas / len(tarefas)
        st.progress(pct, text=f"**{pct:.0%} concluído** · ✅ {concluidas} feita(s) · "
                              f"⏳ {len(tarefas) - concluidas} pendente(s) · {len(tarefas)} no total")

        # Contadores por cômodo (sempre sobre todas as tarefas, independentemente dos filtros).
        st.markdown(
            "".join(badge_ambiente(amb, sum(t["concluida"] for t in itens), len(itens)) for amb, itens in grupos.items()),
            unsafe_allow_html=True,
        )

        # Nunca deixa o usuário "perdido": se o filtro esconde tarefas, avisa e oferece mostrar tudo.
        ocultas = len(tarefas) - len(tarefas_filtradas)
        if filtrando and ocultas:
            with st.container(border=True):
                st.warning(
                    f"🔎 O filtro atual mostra **{len(tarefas_filtradas)} de {len(tarefas)}** tarefa(s); "
                    f"**{ocultas}** estão ocultas." if tarefas_filtradas else
                    f"🔎 Nenhuma tarefa corresponde à busca/filtros atuais. As **{len(tarefas)}** tarefa(s) "
                    "da obra estão ocultas.",
                    icon="⚠️",
                )
                st.button(f"Mostrar todas as {len(tarefas)} tarefas da obra", key=f"mostrar_todas_{obra_id}",
                          on_click=_limpar_filtros, args=(obra_id,), type="primary", width="stretch")

        for ambiente, visiveis in agrupar_por_ambiente(tarefas_filtradas).items():
            itens = grupos.get(ambiente, visiveis)
            feitas_total = sum(t["concluida"] for t in itens)
            pendentes_v = [t for t in visiveis if not t["concluida"]]
            feitas_v = [t for t in visiveis if t["concluida"]]
            with st.container(border=True):
                st.markdown(
                    f"<div class='ot-amb-titulo'>{icone_ambiente(ambiente)} {esc(ambiente, LIM_AMBIENTE)} "
                    f"{badge(f'{feitas_total}/{len(itens)} concluídas', 'completo' if feitas_total == len(itens) else 'amb')}</div>",
                    unsafe_allow_html=True,
                )
                parcial = " (filtradas)" if len(visiveis) < len(itens) else ""
                st.markdown(f"<div class='ot-sec ot-sec-pend'>⏳ Pendente / A Fazer ({len(pendentes_v)}){parcial}</div>",
                            unsafe_allow_html=True)
                if not pendentes_v:
                    st.caption("Nada pendente aqui. 🎉" if not parcial else "Nenhuma pendente no filtro atual.")
                for t in pendentes_v:
                    linha_tarefa(t)
                st.markdown(f"<div class='ot-sec ot-sec-ok'>✅ Feito / Concluído ({len(feitas_v)}){parcial}</div>",
                            unsafe_allow_html=True)
                if not feitas_v:
                    st.caption("Nenhum serviço concluído ainda neste ambiente.")
                for t in feitas_v:
                    linha_tarefa(t)


# --------------------------------------------------------------------------- #
# Aba 3: Histórico e Diários
# --------------------------------------------------------------------------- #
with aba_historico:
    if not registros:
        st.info("Nenhum registro ainda. Use a aba “Novo Registro” para criar o primeiro.")
    elif not registros_filtrados:
        st.info("Nenhum registro com a busca/filtros atuais.")
    for r in registros_filtrados:  # já vem do mais recente para o mais antigo
        card_registro(r, tarefas=mapa_tarefas.get(r["id"]), com_acoes=True, midias=midias_por_registro.get(r["id"]))


# --------------------------------------------------------------------------- #
# Aba 4: Galeria As-Built & Relatório do Cliente
# --------------------------------------------------------------------------- #
def form_adicionar_fotos(chave: str, ambiente_fixo: str | None = None) -> None:
    """Formulário para enviar fotos direto na galeria (num ambiente fixo ou escolhido)."""
    with st.form(chave, clear_on_submit=True, enter_to_submit=False, border=False):
        amb = ambiente_fixo or seletor_ambiente("Ambiente", recomendados, key=f"{chave}_amb")
        opcoes_tarefa = {None: "— Sem tarefa vinculada —"} | {
            t["id"]: f"{'✅' if t['concluida'] else '⏳'} {t['descricao']}"
            for t in tarefas if not ambiente_fixo or t["ambiente"] == ambiente_fixo
        }
        tarefa_id = st.selectbox("Tarefa vinculada (opcional)", list(opcoes_tarefa), format_func=opcoes_tarefa.get,
                                 key=f"{chave}_tarefa", help="A foto aparece junto da tarefa no checklist.")
        titulo = st.text_input("Serviço realizado", max_chars=LIM_TAREFA, key=f"{chave}_titulo",
                               placeholder="Ex.: Retirada da cerâmica do banheiro",
                               help="Vira o subtítulo das fotos na galeria. Em branco: usa o texto da tarefa.")
        descricao = st.text_area("Descrição técnica (opcional)", max_chars=LIMITE_TEXTO_LONGO, key=f"{chave}_desc",
                                 placeholder="Ex.: tubo de água quente a 30 cm do piso, lado esquerdo")
        arquivos = st.file_uploader("Fotos", type=TIPOS_FOTO, accept_multiple_files=True, key=f"{chave}_up",
                                    help=f"Até {MAX_FOTOS_ENVIO} por envio. A localização e os dados do celular são removidos.")
        if st.form_submit_button("📎 Adicionar fotos", type="primary", width="stretch"):
            tarefa = next((t for t in tarefas if t["id"] == tarefa_id), None)
            amb_ok = sanitize_text(amb, LIM_AMBIENTE) or (tarefa["ambiente"] if tarefa else "")
            if not arquivos:
                st.error("Escolha ao menos uma foto.")
            elif not amb_ok:
                st.error("Escolha o ambiente das fotos.")
            else:
                titulo_ok = sanitize_text(titulo, LIM_TAREFA) or (tarefa["descricao"] if tarefa else "Registro fotográfico")
                n, erros = salvar_fotos(arquivos, obra_id, amb_ok, titulo_ok, descricao, tarefa_id=tarefa_id)
                for e in erros:
                    st.warning(e, icon="⚠️")
                if n:
                    flash(f"{n} foto(s) adicionada(s) em {amb_ok}.")
                    st.rerun()


with aba_galeria:
    st.subheader(
        "🖼️ Galeria da Obra (Por Ambiente e Serviço)",
        help="Fotos técnicas com a posição de canos, conduítes e fiação, organizadas por cômodo e serviço. "
        "Consulte antes de furar paredes ou pisos: funciona como manual de manutenção da obra.",
    )
    todas_fotos = midias_com_arquivo(midias_obra)
    if todas_fotos:
        st.download_button(
            "📦 Baixar Fotos da Obra Separadas por Pasta de Ambiente (.ZIP)",
            # Gerado só no clique: subpastas por ambiente + memorial descritivo.
            data=partial(gerar_zip_asbuilt, obra, registros, tarefas, midias_obra),
            file_name=f"fotos_obra_{obra_id}_{date.today():%Y%m%d}.zip",
            mime="application/zip",
            width="stretch",
            type="primary",
            on_click="ignore",
            help="Todas as fotos em pastas por cômodo (Banheiro_Social/01_2026-11-11_retirada_da_ceramica.jpg, "
            "Cozinha/...) e o Memorial_Descritivo_AsBuilt.txt com a descrição técnica de cada foto.",
        )
    with st.expander("➕ Adicionar fotos na galeria", expanded=not todas_fotos):
        form_adicionar_fotos(f"form_galeria_nova_{obra_id}")

    if not todas_fotos:
        st.info("Nenhuma foto ainda. Envie fotos aqui, na aba “Novo Registro” ou pelo 📷 de cada tarefa do checklist.")
    else:
        ids_ok = {m["id"] for m in filtrar_midias(midias_obra)}  # busca global + filtros avançados
        fotos_filtradas = [(m, p) for m, p in todas_fotos if m["id"] in ids_ok]
        g_amb, g_txt = st.columns([1, 2])
        opcoes_amb_galeria = [TODOS_AMBIENTES, *agrupar_midias(todas_fotos)]
        chave_g_amb = f"galeria_amb_{obra_id}"
        if st.session_state.get(chave_g_amb) not in opcoes_amb_galeria:
            st.session_state[chave_g_amb] = TODOS_AMBIENTES
        amb_galeria = g_amb.selectbox("Ambiente", opcoes_amb_galeria, key=chave_g_amb,
                                      help="Mostra só as fotos de um cômodo.")
        termo_galeria = g_txt.text_input(
            "Palavra-chave", placeholder="Ex.: cerâmica, água quente, conduíte, 30 cm", key=f"galeria_txt_{obra_id}",
            max_chars=LIM_BUSCA, help="Procura no ambiente, serviço, descrição técnica, tarefa e relato do dia.",
        )
        termo_galeria = sanitize_text(termo_galeria, LIM_BUSCA)
        fotos_filtradas = [
            (m, p) for m, p in fotos_filtradas
            if (amb_galeria == TODOS_AMBIENTES or m["ambiente"] == amb_galeria) and midia_corresponde(m, termo_galeria)
        ]
        st.caption(f"{len(fotos_filtradas)} de {len(todas_fotos)} foto(s)")
        if not fotos_filtradas:
            st.info("Nenhuma foto com esses filtros.")
            st.button("Mostrar todas as fotos", key=f"galeria_mostrar_todas_{obra_id}",
                      on_click=_limpar_filtros, args=(obra_id,), width="stretch")

        for ambiente, servicos in agrupar_midias(fotos_filtradas).items():
            qtd = sum(len(v) for v in servicos.values())
            with st.container(border=True):
                st.markdown(f"### {icone_ambiente(ambiente)} {esc(ambiente, LIM_AMBIENTE)}")
                st.caption(f"{qtd} foto(s) · {len(servicos)} serviço(s)")
                for (servico, data_m), itens in servicos.items():
                    st.markdown(
                        f"<div class='ot-servico'>{esc(servico, LIM_TAREFA)} — {esc(formatar_data(data_m))}</div>",
                        unsafe_allow_html=True,
                    )
                    grade_fotos(itens, prefixo=f"gal_{obra_id}")
                with st.popover(f"➕ Adicionar mais fotos em {ambiente}", width="stretch"):
                    form_adicionar_fotos(f"form_galeria_{obra_id}_{slug(ambiente, '_')}", ambiente_fixo=ambiente)

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
        data=partial(gerar_relatorio_html, obra, registros, tarefas, midias_obra),
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
