"""Camada de persistência SQLite do ObraTrack AI."""

from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
import warnings
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "obratrack.db"
UPLOADS_DIR = BASE_DIR / "uploads"
GITIGNORE_PATH = BASE_DIR / ".gitignore"

STATUS_OBRA = ("Em Andamento", "Pausada", "Concluída")

AMBIENTES_BASE = (
    "Banheiro Social", "Banheiro Suíte", "Cozinha", "Sala", "Quarto",
    "Área de Serviço", "Área Externa", "Hidráulica Geral", "Elétrica Geral",
)
STATUS_TAREFA = ("Todas", "Pendentes", "Concluídas")

# Entradas que obrigatoriamente precisam estar no .gitignore (dados sensíveis).
GITIGNORE_OBRIGATORIO = ("*.db", "*.db-wal", "*.db-shm", ".env", "uploads/")


# --------------------------------------------------------------------------- #
# Segurança de caminhos
# --------------------------------------------------------------------------- #
class CaminhoInseguroError(RuntimeError):
    """Caminho fora do diretório local restrito do projeto."""


def _em_unidade_de_rede(caminho: Path) -> bool:
    """True para compartilhamentos UNC (\\\\servidor\\...) ou unidades de rede mapeadas no Windows."""
    texto = str(caminho)
    if texto.startswith("\\\\"):
        return True
    if os.name == "nt" and caminho.drive:
        import ctypes

        DRIVE_REMOTE = 4
        return ctypes.windll.kernel32.GetDriveTypeW(f"{caminho.drive}\\") == DRIVE_REMOTE
    return False


def validar_caminho_local(caminho: Path, raiz: Path = BASE_DIR) -> Path:
    """Garante que `caminho` (após resolver symlinks e '..') fica dentro de `raiz`, em disco local."""
    resolvido = Path(caminho).resolve()
    raiz_resolvida = raiz.resolve()
    if not resolvido.is_relative_to(raiz_resolvida):
        raise CaminhoInseguroError(f"Caminho fora do diretório permitido ({raiz_resolvida}): {resolvido}")
    if _em_unidade_de_rede(resolvido):
        raise CaminhoInseguroError(f"Caminho em unidade de rede não é permitido: {resolvido}")
    return resolvido


def caminho_midia_seguro(caminho: str | Path | None) -> Optional[Path]:
    """Resolve um caminho de mídia salvo no banco e só o aceita se estiver dentro de uploads\\."""
    if not caminho:
        return None
    p = Path(caminho)
    p = p if p.is_absolute() else BASE_DIR / p
    try:
        return validar_caminho_local(p, UPLOADS_DIR)
    except CaminhoInseguroError:
        return None


def _restringir_permissoes(caminho: Path, modo: int) -> None:
    """Em Linux/macOS aplica permissão só para o dono. No Windows o arquivo herda a ACL
    do perfil do usuário (C:\\Users\\<usuario>), que já é restrita a ele e aos administradores."""
    if os.name != "nt" and caminho.exists():
        os.chmod(caminho, modo)


def verificar_gitignore() -> list[str]:
    """Retorna as entradas obrigatórias ausentes do .gitignore (lista vazia = OK)."""
    if not GITIGNORE_PATH.is_file():
        return list(GITIGNORE_OBRIGATORIO)
    linhas = {
        linha.strip()
        for linha in GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
        if linha.strip() and not linha.lstrip().startswith("#")
    }
    return [entrada for entrada in GITIGNORE_OBRIGATORIO if entrada not in linhas]


def normalizar_busca(texto: object) -> str:
    """Minúsculas e sem acentos, para comparar textos digitados no canteiro."""
    if texto is None:
        return ""
    decomposto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()


def corresponde_busca(busca: Optional[str], *campos: object) -> bool:
    """True se TODAS as palavras da busca aparecem em algum dos campos (sem acentos/maiúsculas).

    Ex.: corresponde_busca("ceramica banheiro", "Retirada da cerâmica", "Banheiro Social") -> True.
    """
    termos = normalizar_busca(busca or "").split()[:10]
    if not termos:
        return True
    alvo = " ".join(normalizar_busca(c) for c in campos if c)
    return all(t in alvo for t in termos)


# --------------------------------------------------------------------------- #
# Detecção de ambiente (cômodo) por palavras-chave
# Usada pela análise offline, pela correção da saída da IA e pela migração das fotos.
# --------------------------------------------------------------------------- #
# Ordem importa: do mais específico para o mais genérico. Regex sobre texto normalizado.
_REGRAS_AMBIENTE: list[tuple[str, str]] = [
    (r"banheiro\s+(?:da\s+|do\s+)?suite|\bsuite\b", "Banheiro Suíte"),
    (r"banheiro\s+social", "Banheiro Social"),
    (r"\blavabo", "Lavabo"),
    (r"\bbanheiro|\bwc\b|\bchuveiro|vaso\s+sanitario|\bprivada|\bbox\b", "Banheiro"),
    (r"\bcozinha", "Cozinha"),
    (r"area\s+de\s+servico|lavanderia|\btanque\b", "Área de Serviço"),
    (r"\bsala\b", "Sala"),
    (r"\bquartos?\b|dormitorio", "Quarto"),
    (r"\bvaranda|\bsacada", "Varanda"),
    (r"\bgaragem", "Garagem"),
    (r"\bfachada", "Fachada"),
    (r"\bmuros?\b", "Muro"),
    (r"\bcalcada", "Calçada"),
    (r"\btelhado|\blaje\b|\bcobertura\b", "Cobertura"),
    (r"area\s+externa|\bquintal|\bjardim|\bfossas?\b|\bvalas?\b|sumidouro|\baneis\b|biodigestor", "Área Externa"),
]
AMBIENTES_EXTERNOS = frozenset({"Área Externa", "Muro", "Calçada", "Fachada", "Cobertura", "Garagem"})
# Ambientes "coringa" que a IA às vezes usa quando não entende o cômodo.
_AMBIENTES_GENERICOS = frozenset(
    normalizar_busca(a) for a in ("", "Geral", "Obra", "Área Externa", "Externa", "Externo",
                                  "Hidráulica Geral", "Elétrica Geral", "Não informado")
)
# Serviços tipicamente internos: com eles, o ambiente nunca deve ser "Área Externa".
_SERVICO_INTERNO = re.compile(
    r"\bceramica|\bazulej|\bpiso\b|porcelanato|revestimento|rejunt|\bcanos?\b|\btubo|tubulac|hidraulic|"
    r"\besgoto|\bralo|\bloucas?\b|\bpia\b|torneira|\bgesso|\bforro|\breboco|\btomadas?\b|interruptor|"
    r"\bfios?\b|fiacao|conduite|\bbancada|\bregistro\s+de\s+agua"
)
_SERVICO_HIDRAULICO = re.compile(r"\bcanos?\b|\btubo|tubulac|hidraulic|\besgoto|\bralo|registro\s+de\s+agua")
_SERVICO_ELETRICO = re.compile(r"\btomadas?\b|interruptor|\bfios?\b|fiacao|conduite|disjuntor|eletric")


def ambientes_citados(texto: object) -> list[str]:
    """Ambientes citados no texto, na ordem em que aparecem (sem repetição)."""
    alvo = normalizar_busca(texto)
    achados: list[tuple[int, str]] = []
    ocupado: list[tuple[int, int]] = []
    for padrao, nome in _REGRAS_AMBIENTE:
        for m in re.finditer(padrao, alvo):
            # Um trecho já reconhecido por regra mais específica não conta de novo
            # ("banheiro social" não vira também "Banheiro").
            if any(m.start() < fim and m.end() > ini for ini, fim in ocupado):
                continue
            ocupado.append((m.start(), m.end()))
            achados.append((m.start(), nome))
    return list(dict.fromkeys(nome for _, nome in sorted(achados)))


def eh_servico_interno(texto: object) -> bool:
    """True para serviços tipicamente internos (cerâmica, piso, cano, tomada, fiação...)."""
    return bool(_SERVICO_INTERNO.search(normalizar_busca(texto)))


def _mesmo_ambiente(a: str, b: str) -> bool:
    na, nb = normalizar_busca(a), normalizar_busca(b)
    return bool(na and nb) and (na in nb or nb in na)


def detectar_ambiente(texto: object, contexto: object = "", padrao: str = "Geral") -> str:
    """Ambiente de uma atividade a partir do texto e, se preciso, do escopo da obra.

    1) cômodo citado no próprio texto ("cerâmica do banheiro" -> Banheiro);
    2) serviço interno sem cômodo (cerâmica, piso, cano...) -> cômodo do contexto/escopo;
    3) hidráulica/elétrica sem cômodo -> "Hidráulica Geral" / "Elétrica Geral";
    4) caso contrário, `padrao`.
    """
    citados = ambientes_citados(texto)
    if citados:
        return citados[0]
    alvo = normalizar_busca(texto)
    if _SERVICO_INTERNO.search(alvo):
        internos = [a for a in ambientes_citados(contexto) if a not in AMBIENTES_EXTERNOS]
        if internos:
            return internos[0]
        if _SERVICO_HIDRAULICO.search(alvo):
            return "Hidráulica Geral"
        if _SERVICO_ELETRICO.search(alvo):
            return "Elétrica Geral"
    return padrao


def corrigir_ambiente(ambiente: Optional[str], descricao: object, relato: object = "", contexto: object = "") -> str:
    """Corrige o ambiente sugerido (pela IA ou por regra) com base nas palavras-chave.

    - Se a descrição da atividade cita um cômodo, ele prevalece ("Retirada da cerâmica do
      banheiro" nunca fica em "Área Externa").
    - Se o ambiente é genérico (Geral, Área Externa, Hidráulica Geral...) e a atividade é um
      serviço interno (cerâmica, piso, cano...), usa o cômodo citado no relato do dia ou,
      sem cômodo no relato, o do escopo da obra.
    """
    ambiente = (ambiente or "").strip()
    desc = normalizar_busca(descricao)
    citados = ambientes_citados(descricao)
    if citados:
        return ambiente if ambiente and _mesmo_ambiente(ambiente, citados[0]) else citados[0]
    if normalizar_busca(ambiente) in _AMBIENTES_GENERICOS:
        interno = bool(_SERVICO_INTERNO.search(desc))
        coringa = normalizar_busca(ambiente) in {"", "geral", "obra", "area externa", "externa", "externo"}
        if interno or coringa:
            do_relato = [a for a in ambientes_citados(relato) if a not in AMBIENTES_EXTERNOS]
            # Vários cômodos no relato: só decide por um deles se o ambiente atual for coringa.
            if len(do_relato) == 1 or (interno and coringa and do_relato):
                return do_relato[0]
            if interno:
                sugerido = detectar_ambiente(descricao, contexto, padrao=ambiente or "Geral")
                if sugerido != "Área Externa":
                    return sugerido
    return ambiente or "Geral"


@contextmanager
def get_connection(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Abre uma conexão com FK e WAL habilitados, commit automático e fechamento garantido.

    WAL (write-ahead log) mantém o banco íntegro se o processo cair no meio de uma
    gravação e permite leituras enquanto outra sessão grava.
    """
    conn = sqlite3.connect(str(validar_caminho_local(db_path)), timeout=10)
    conn.row_factory = sqlite3.Row
    # norm(): busca sem diferenciar maiúsculas nem acentos ("ceramica" acha "Cerâmica").
    conn.create_function("norm", 1, normalizar_busca, deterministic=True)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Valida o ambiente local, cria as tabelas (se não existirem) e a pasta de uploads."""
    validar_caminho_local(DB_PATH)
    validar_caminho_local(UPLOADS_DIR)
    faltando = verificar_gitignore()
    if faltando:
        warnings.warn(
            f".gitignore sem as entradas obrigatórias {faltando}: banco, chave ou fotos podem ir para o Git.",
            stacklevel=2,
        )

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    _restringir_permissoes(UPLOADS_DIR, 0o700)
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS clientes_obras (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                nome_cliente   TEXT NOT NULL,
                endereco       TEXT,
                escopo_resumo  TEXT,
                status         TEXT NOT NULL DEFAULT 'Em Andamento'
                               CHECK (status IN ('Em Andamento', 'Pausada', 'Concluída')),
                data_inicio    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS registros_diarios (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                obra_id              INTEGER NOT NULL,
                data_registro        TEXT NOT NULL,
                transcricao_audio    TEXT,
                resumo_executivo     TEXT,
                motivo_atraso        TEXT,
                detalhes_execucao_imprevistos TEXT,
                mapeamento_asbuilt   TEXT,
                caminho_foto         TEXT,
                caminho_audio        TEXT,
                foto_bytes_original  INTEGER,
                FOREIGN KEY (obra_id) REFERENCES clientes_obras (id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_registros_obra_id
                ON registros_diarios (obra_id);

            -- Checklist de tarefas por ambiente. Excluir a obra apaga as tarefas;
            -- excluir o registro de origem apenas desvincula a tarefa (registro_id -> NULL).
            CREATE TABLE IF NOT EXISTS tarefas_obra (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                obra_id       INTEGER NOT NULL,
                registro_id   INTEGER,
                ambiente      TEXT NOT NULL DEFAULT 'Geral',
                descricao     TEXT NOT NULL,
                concluida     INTEGER NOT NULL DEFAULT 0 CHECK (concluida IN (0, 1)),
                data_criacao  TEXT NOT NULL,
                data_conclusao TEXT,
                resumo_servico TEXT,
                detalhes_servico TEXT,
                FOREIGN KEY (obra_id) REFERENCES clientes_obras (id) ON DELETE CASCADE,
                FOREIGN KEY (registro_id) REFERENCES registros_diarios (id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tarefas_obra_id ON tarefas_obra (obra_id);
            CREATE INDEX IF NOT EXISTS idx_tarefas_registro_id ON tarefas_obra (registro_id);

            -- Fotos da obra vinculadas a ambiente, serviço e (opcionalmente) registro/tarefa.
            -- Um registro ou tarefa pode ter várias fotos. Excluir a tarefa só desvincula a foto.
            CREATE TABLE IF NOT EXISTS midias_obra (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                obra_id              INTEGER NOT NULL,
                registro_id          INTEGER,
                tarefa_id            INTEGER,
                ambiente             TEXT NOT NULL DEFAULT 'Geral',
                titulo_servico       TEXT,
                descricao_tecnica    TEXT,
                caminho_foto         TEXT NOT NULL,
                data_midia           TEXT NOT NULL,
                foto_bytes_original  INTEGER,
                vinculo_ia           INTEGER,
                FOREIGN KEY (obra_id) REFERENCES clientes_obras (id) ON DELETE CASCADE,
                FOREIGN KEY (registro_id) REFERENCES registros_diarios (id) ON DELETE SET NULL,
                FOREIGN KEY (tarefa_id) REFERENCES tarefas_obra (id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_midias_obra_id ON midias_obra (obra_id);
            CREATE INDEX IF NOT EXISTS idx_midias_registro_id ON midias_obra (registro_id);
            CREATE INDEX IF NOT EXISTS idx_midias_tarefa_id ON midias_obra (tarefa_id);
            """
        )
        _migrar(conn)
    _restringir_permissoes(DB_PATH, 0o600)


def _migrar(conn: sqlite3.Connection) -> None:
    """Migrações idempotentes para bancos criados por versões anteriores."""
    colunas = {row["name"] for row in conn.execute("PRAGMA table_info(registros_diarios)")}
    if "caminho_audio" not in colunas:
        conn.execute("ALTER TABLE registros_diarios ADD COLUMN caminho_audio TEXT")
    if "foto_bytes_original" not in colunas:
        # Tamanho da foto como saiu do celular, para mostrar a economia da compressão.
        conn.execute("ALTER TABLE registros_diarios ADD COLUMN foto_bytes_original INTEGER")
    if "detalhes_execucao_imprevistos" not in colunas:
        # Substitui a antiga nota de produtividade (Alta/Média/Baixa) por um texto factual.
        # A coluna legada `nivel_produtividade`, se existir, fica no banco sem uso.
        conn.execute("ALTER TABLE registros_diarios ADD COLUMN detalhes_execucao_imprevistos TEXT")
    colunas_t = {row["name"] for row in conn.execute("PRAGMA table_info(tarefas_obra)")}
    if "data_conclusao" not in colunas_t:
        # Dia em que a tarefa foi marcada como feita no checklist (NULL: vale a data do registro).
        conn.execute("ALTER TABLE tarefas_obra ADD COLUMN data_conclusao TEXT")
    # Resumo e detalhes exclusivos da tarefa (um relato pode citar banheiro E vala: cada
    # tarefa guarda só o que foi dito sobre ela).
    for coluna in ("resumo_servico", "detalhes_servico"):
        if coluna not in colunas_t:
            conn.execute(f"ALTER TABLE tarefas_obra ADD COLUMN {coluna} TEXT")
    colunas_m = {row["name"] for row in conn.execute("PRAGMA table_info(midias_obra)")}
    if "vinculo_ia" not in colunas_m:
        # 1 = a IA viu a imagem e decidiu o vínculo com a tarefa (ou que não mostra nenhuma tarefa).
        # NULL = foto antiga ou manual: continua com as regras de vínculo por ambiente de antes.
        conn.execute("ALTER TABLE midias_obra ADD COLUMN vinculo_ia INTEGER")
    _sanear_textos_legados(conn)
    _migrar_fotos_para_midias(conn)
    _corrigir_asbuilt_sem_foto(conn)
    for (registro_id,) in conn.execute(
        """SELECT DISTINCT m.registro_id FROM midias_obra m
           WHERE m.registro_id IS NOT NULL AND m.tarefa_id IS NULL AND m.ambiente = 'Geral'
             AND EXISTS (SELECT 1 FROM tarefas_obra t WHERE t.registro_id = m.registro_id)"""
    ).fetchall():
        _vincular_fotos_soltas(conn, registro_id)


def _migrar_fotos_para_midias(conn: sqlite3.Connection) -> int:
    """Copia para `midias_obra` as fotos de registros antigos que ainda não estão lá.

    O ambiente vem das tarefas extraídas do registro; se elas forem genéricas ("Área
    Externa"/"Geral") e o texto do dia citar um cômodo interno (banheiro, cozinha...),
    vale o cômodo do texto. Sem tarefas, usa o texto do registro e o escopo da obra.
    Idempotente: a foto é identificada pelo caminho do arquivo.
    """
    pendentes = conn.execute(
        """
        SELECT r.id, r.obra_id, r.data_registro, r.transcricao_audio, r.resumo_executivo,
               r.mapeamento_asbuilt, r.caminho_foto, r.foto_bytes_original, o.escopo_resumo
        FROM registros_diarios r
        JOIN clientes_obras o ON o.id = r.obra_id
        WHERE r.caminho_foto IS NOT NULL AND TRIM(r.caminho_foto) <> ''
          AND NOT EXISTS (SELECT 1 FROM midias_obra m WHERE m.caminho_foto = r.caminho_foto)
        """
    ).fetchall()
    for r in pendentes:
        texto = " ".join(filter(None, (r["transcricao_audio"], r["resumo_executivo"], r["mapeamento_asbuilt"])))
        tarefas = conn.execute(
            "SELECT id, ambiente, descricao, concluida FROM tarefas_obra WHERE registro_id = ? "
            "ORDER BY concluida DESC, id",
            (r["id"],),
        ).fetchall()
        if tarefas:
            tarefa = tarefas[0]
            ambiente = corrigir_ambiente(tarefa["ambiente"], tarefa["descricao"], texto, r["escopo_resumo"])
            titulo, tarefa_id = tarefa["descricao"], tarefa["id"]
        else:
            ambiente = detectar_ambiente(texto, r["escopo_resumo"])
            if ambiente in ("Geral", "Área Externa"):
                ambiente = corrigir_ambiente(ambiente, texto, texto, r["escopo_resumo"])
            titulo, tarefa_id = (r["resumo_executivo"] or "Registro fotográfico do dia")[:120], None
        conn.execute(
            """
            INSERT INTO midias_obra (obra_id, registro_id, tarefa_id, ambiente, titulo_servico,
                                     descricao_tecnica, caminho_foto, data_midia, foto_bytes_original)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (r["obra_id"], r["id"], tarefa_id, ambiente, titulo, r["mapeamento_asbuilt"] or "",
             r["caminho_foto"], r["data_registro"], r["foto_bytes_original"]),
        )
    return len(pendentes)


# Frases geradas pelo antigo "Modo Demonstração" que expunham detalhes técnicos ao usuário.
# Só essas frases exatas (produzidas pelo próprio app) são reescritas.
_TRANSCRICAO_NOVA = (
    "Transcrição automática indisponível para este registro. Ouça o relato original no "
    "player ou edite o registro para incluir a transcrição."
)
_SUBSTITUICOES_LEGADAS = [
    ("transcricao_audio",
     "[Modo Demonstração] Áudio gravado e arquivado no registro; a transcrição automática requer a GEMINI_API_KEY no servidor. Ouça o relato original no player.",
     _TRANSCRICAO_NOVA),
    ("transcricao_audio",
     "[Modo Demonstração] Áudio gravado e arquivado no registro; a transcrição automática requer a GEMINI_API_KEY. Ouça o relato original no player.",
     _TRANSCRICAO_NOVA),
    ("mapeamento_asbuilt",
     "A identificação automática de tubulações, conduítes e fiação requer a análise de IA (GEMINI_API_KEY no servidor).",
     "A descrição da posição de tubulações, conduítes e fiação pode ser completada editando este registro."),
    ("mapeamento_asbuilt",
     "A identificação automática de tubulações, conduítes e fiação requer a análise de IA (configure a GEMINI_API_KEY).",
     "A descrição da posição de tubulações, conduítes e fiação pode ser completada editando este registro."),
    ("resumo_executivo", "[Modo Demonstração] ", ""),
]


SEM_FOTO_ASBUILT = "Sem foto para mapeamento As-Built"
ASBUILT_FOTO_SEM_DESCRICAO = (
    "Fotos do dia arquivadas como evidência As-Built. A descrição da posição de tubulações, "
    "conduítes e fiação pode ser completada na Galeria da Obra."
)


def _corrigir_asbuilt_sem_foto(conn: sqlite3.Connection) -> None:
    """Migração idempotente: "Sem foto para mapeamento As-Built" não pode aparecer quando
    há foto. Limpa a descrição das fotos que herdaram essa frase e ajusta o registro."""
    conn.execute(
        "UPDATE midias_obra SET descricao_tecnica = '' WHERE TRIM(descricao_tecnica) = ?", (SEM_FOTO_ASBUILT,)
    )
    conn.execute(
        """
        UPDATE registros_diarios SET mapeamento_asbuilt = ?
        WHERE TRIM(mapeamento_asbuilt) = ?
          AND (TRIM(COALESCE(caminho_foto, '')) <> ''
               OR EXISTS (SELECT 1 FROM midias_obra m WHERE m.registro_id = registros_diarios.id))
        """,
        (ASBUILT_FOTO_SEM_DESCRICAO, SEM_FOTO_ASBUILT),
    )


def _sanear_textos_legados(conn: sqlite3.Connection) -> None:
    """Migração idempotente: remove de registros antigos as menções técnicas do modo offline."""
    for coluna, antigo, novo in _SUBSTITUICOES_LEGADAS:
        conn.execute(
            f"UPDATE registros_diarios SET {coluna} = REPLACE({coluna}, ?, ?) WHERE instr({coluna}, ?) > 0",
            (antigo, novo, antigo),
        )


def _validar_status(status: str) -> None:
    if status not in STATUS_OBRA:
        raise ValueError(f"Status inválido: {status!r}. Use um de {STATUS_OBRA}.")


def _remover_midias_orfas(conn: sqlite3.Connection, caminhos: list[Optional[str]]) -> int:
    """Apaga do disco as mídias que nenhum registro referencia mais.

    Só remove arquivos dentro de uploads\\ (caminho_midia_seguro). Deve ser chamada
    depois do DELETE, na mesma conexão, para enxergar o estado já atualizado.
    """
    removidos = 0
    for caminho in {c for c in caminhos if c}:
        ainda_usado = conn.execute(
            """
            SELECT 1 FROM registros_diarios WHERE caminho_foto = ? OR caminho_audio = ?
            UNION ALL SELECT 1 FROM midias_obra WHERE caminho_foto = ?
            LIMIT 1
            """,
            (caminho, caminho, caminho),
        ).fetchone()
        arquivo = caminho_midia_seguro(caminho)
        if ainda_usado or arquivo is None or not arquivo.is_file():
            continue
        try:
            arquivo.unlink()
            removidos += 1
        except OSError:
            pass  # arquivo em uso (ex.: aberto no player); fica para uma limpeza futura
    return removidos


# --------------------------------------------------------------------------- #
# Obras (Blocos de Clientes)
# --------------------------------------------------------------------------- #
def listar_obras() -> list[dict]:
    """Retorna todas as obras, das mais recentes para as mais antigas."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM clientes_obras ORDER BY data_inicio DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def resumo_obras() -> dict[int, dict]:
    """Indicadores de todas as obras numa só consulta (para os cards dos blocos).

    Retorna {obra_id: {"tarefas": n, "concluidas": n, "registros": n}}.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT o.id,
                   (SELECT COUNT(*) FROM tarefas_obra t WHERE t.obra_id = o.id) AS tarefas,
                   (SELECT COALESCE(SUM(t.concluida), 0) FROM tarefas_obra t WHERE t.obra_id = o.id) AS concluidas,
                   (SELECT COUNT(*) FROM registros_diarios r WHERE r.obra_id = o.id) AS registros
            FROM clientes_obras o
            """
        ).fetchall()
    return {r["id"]: {"tarefas": r["tarefas"], "concluidas": r["concluidas"], "registros": r["registros"]} for r in rows}


def criar_obra(
    nome_cliente: str,
    endereco: str = "",
    escopo_resumo: str = "",
    status: str = "Em Andamento",
    data_inicio: Optional[str] = None,
) -> int:
    """Cria uma nova obra e retorna o seu id."""
    _validar_status(status)
    data_inicio = data_inicio or date.today().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO clientes_obras (nome_cliente, endereco, escopo_resumo, status, data_inicio)
            VALUES (?, ?, ?, ?, ?)
            """,
            (nome_cliente, endereco, escopo_resumo, status, data_inicio),
        )
        return int(cur.lastrowid)


def editar_obra(
    obra_id: int,
    nome_cliente: str,
    endereco: str,
    escopo_resumo: str,
    status: str,
    data_inicio: str,
) -> bool:
    """Atualiza os dados de uma obra. Retorna False se ela não existir."""
    _validar_status(status)
    if not nome_cliente.strip():
        raise ValueError("O nome da cliente/obra é obrigatório.")
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE clientes_obras
            SET nome_cliente = ?, endereco = ?, escopo_resumo = ?, status = ?, data_inicio = ?
            WHERE id = ?
            """,
            (nome_cliente.strip(), endereco, escopo_resumo, status, data_inicio, obra_id),
        )
        return cur.rowcount > 0


def excluir_obra(obra_id: int) -> bool:
    """Exclui a obra com todos os seus registros, tarefas e mídias. Retorna False se não existir."""
    with get_connection() as conn:
        midias = [
            c
            for row in conn.execute(
                "SELECT caminho_foto, caminho_audio FROM registros_diarios WHERE obra_id = ?", (obra_id,)
            )
            for c in (row["caminho_foto"], row["caminho_audio"])
        ] + [row["caminho_foto"] for row in conn.execute("SELECT caminho_foto FROM midias_obra WHERE obra_id = ?", (obra_id,))]
        # Exclusão explícita (não depende de ON DELETE CASCADE em bancos antigos).
        conn.execute("DELETE FROM midias_obra WHERE obra_id = ?", (obra_id,))
        conn.execute("DELETE FROM tarefas_obra WHERE obra_id = ?", (obra_id,))
        conn.execute("DELETE FROM registros_diarios WHERE obra_id = ?", (obra_id,))
        cur = conn.execute("DELETE FROM clientes_obras WHERE id = ?", (obra_id,))
        if cur.rowcount:
            _remover_midias_orfas(conn, midias)
        return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Registros diários
# --------------------------------------------------------------------------- #
def inserir_registro(
    obra_id: int,
    transcricao_audio: str = "",
    resumo_executivo: str = "",
    motivo_atraso: str = "",
    detalhes_execucao_imprevistos: str = "",
    mapeamento_asbuilt: str = "",
    caminho_foto: Optional[str | Path] = None,
    caminho_audio: Optional[str | Path] = None,
    data_registro: Optional[str] = None,
    foto_bytes_original: Optional[int] = None,
) -> int:
    """Insere um registro diário vinculado a uma obra e retorna o seu id."""
    data_registro = data_registro or date.today().isoformat()
    # Só aceita mídias dentro de uploads\ (impede gravar caminhos para arquivos arbitrários do disco).
    for rotulo, caminho in (("foto", caminho_foto), ("áudio", caminho_audio)):
        if caminho and caminho_midia_seguro(caminho) is None:
            raise CaminhoInseguroError(f"Caminho de {rotulo} fora da pasta uploads: {caminho}")
    foto = str(Path(caminho_foto)) if caminho_foto else None
    audio = str(Path(caminho_audio)) if caminho_audio else None
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO registros_diarios (
                obra_id, data_registro, transcricao_audio, resumo_executivo,
                motivo_atraso, detalhes_execucao_imprevistos, mapeamento_asbuilt,
                caminho_foto, caminho_audio, foto_bytes_original
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obra_id,
                data_registro,
                transcricao_audio,
                resumo_executivo,
                motivo_atraso,
                detalhes_execucao_imprevistos,
                mapeamento_asbuilt,
                foto,
                audio,
                foto_bytes_original if foto else None,
            ),
        )
        return int(cur.lastrowid)


_COLUNAS_REGISTRO = """r.id, r.obra_id, r.data_registro, r.transcricao_audio, r.resumo_executivo,
                   r.motivo_atraso, r.detalhes_execucao_imprevistos, r.mapeamento_asbuilt,
                   r.caminho_foto, r.caminho_audio, r.foto_bytes_original"""


def listar_registros_por_obra(obra_id: int) -> list[dict]:
    """Retorna os registros diários de uma obra, do mais recente ao mais antigo."""
    return buscar_registros(obra_id)


def _like(texto: str) -> str:
    """Padrão LIKE '%texto%' normalizado, com os curingas do usuário escapados."""
    t = normalizar_busca(texto).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{t}%"


def _termos(busca: Optional[str]) -> list[str]:
    """Uma condição por palavra da busca (máx. 10): todas precisam aparecer."""
    return [_like(p) for p in normalizar_busca(busca or "").split()[:10]]


def _filtro_atraso(sem_atraso: str) -> tuple[str, list]:
    return ("TRIM(COALESCE(r.motivo_atraso, '')) NOT IN ('', ?)", [sem_atraso])


def buscar_registros(
    obra_id: int,
    busca: str = "",
    ambientes: Optional[list[str]] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    apenas_atraso: bool = False,
    texto_sem_atraso: str = "",
) -> list[dict]:
    """Registros da obra que atendem a todos os filtros, do mais recente ao mais antigo.

    - `busca`: cada palavra precisa aparecer na transcrição, resumo, atraso, As-Built
      ou nas tarefas extraídas do registro (sem diferenciar maiúsculas nem acentos).
    - `ambientes`: registros com tarefa nesses ambientes ou que os citam no texto.
    - `apenas_atraso`: só dias com justificativa de atraso diferente de `texto_sem_atraso`.
    """
    where, params = ["r.obra_id = ?"], [obra_id]
    textos = ("r.transcricao_audio", "r.resumo_executivo", "r.motivo_atraso",
              "r.detalhes_execucao_imprevistos", "r.mapeamento_asbuilt")
    for termo in _termos(busca):
        campos = " OR ".join(f"norm({c}) LIKE ? ESCAPE '\\'" for c in textos)
        where.append(
            f"""({campos} OR EXISTS (
                    SELECT 1 FROM tarefas_obra t WHERE t.registro_id = r.id
                    AND (norm(t.descricao) LIKE ? ESCAPE '\\' OR norm(t.ambiente) LIKE ? ESCAPE '\\'))
                 OR EXISTS (
                    SELECT 1 FROM midias_obra m WHERE m.registro_id = r.id
                    AND (norm(m.titulo_servico) LIKE ? ESCAPE '\\' OR norm(m.ambiente) LIKE ? ESCAPE '\\')))"""
        )
        params += [termo] * (len(textos) + 4)
    if ambientes:
        marcas = ", ".join("?" * len(ambientes))
        cita = " OR ".join(
            "norm(r.mapeamento_asbuilt) LIKE ? ESCAPE '\\' OR norm(r.resumo_executivo) LIKE ? ESCAPE '\\'"
            for _ in ambientes
        )
        where.append(
            f"""(EXISTS (SELECT 1 FROM tarefas_obra t WHERE t.registro_id = r.id AND t.ambiente IN ({marcas}))
                 OR {cita})"""
        )
        params += list(ambientes)
        for a in ambientes:
            params += [_like(a)] * 2
    if data_inicio:
        where.append("r.data_registro >= ?")
        params.append(data_inicio)
    if data_fim:
        where.append("r.data_registro <= ?")
        params.append(data_fim)
    if apenas_atraso:
        sql, p = _filtro_atraso(texto_sem_atraso)
        where.append(sql)
        params += p
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {_COLUNAS_REGISTRO} FROM registros_diarios r WHERE {' AND '.join(where)} "
            "ORDER BY r.data_registro DESC, r.id DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def atualizar_registro_diario(
    registro_id: int,
    data_registro: str,
    transcricao_audio: str,
    resumo_executivo: str,
    motivo_atraso: str,
    detalhes_execucao_imprevistos: str,
    mapeamento_asbuilt: str,
) -> bool:
    """Atualiza os campos textuais de um registro (mídias não mudam). False se não existir."""
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE registros_diarios
            SET data_registro = ?, transcricao_audio = ?, resumo_executivo = ?,
                motivo_atraso = ?, detalhes_execucao_imprevistos = ?, mapeamento_asbuilt = ?
            WHERE id = ?
            """,
            (
                data_registro,
                transcricao_audio,
                resumo_executivo,
                motivo_atraso,
                detalhes_execucao_imprevistos,
                mapeamento_asbuilt,
                registro_id,
            ),
        )
        return cur.rowcount > 0


def atualizar_transcricao_registro(registro_id: int, transcricao_audio: str) -> bool:
    """Grava a transcrição do áudio de um registro (ex.: áudio transcrito de novo). False se não existir."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE registros_diarios SET transcricao_audio = ? WHERE id = ?", (transcricao_audio, registro_id)
        )
        return cur.rowcount > 0


def _vincular_fotos_soltas(conn: sqlite3.Connection, registro_id: int) -> int:
    """Liga as fotos do registro sem tarefa à tarefa certa (mesmo ambiente) ou, se a foto
    está em "Geral", à tarefa principal do registro (concluída primeiro), levando o ambiente.
    Fotos sem tarefa em outro ambiente específico ficam como estão. Retorna quantas mudaram."""
    tarefas = conn.execute(
        "SELECT id, ambiente, descricao FROM tarefas_obra WHERE registro_id = ? ORDER BY concluida DESC, id",
        (registro_id,),
    ).fetchall()
    if not tarefas:
        return 0
    alteradas = 0
    for m in conn.execute(
        # vinculo_ia = 1: a IA já viu a imagem e decidiu que ela não mostra nenhuma tarefa; não forçar.
        "SELECT id, ambiente, titulo_servico FROM midias_obra "
        "WHERE registro_id = ? AND tarefa_id IS NULL AND vinculo_ia IS NULL",
        (registro_id,),
    ).fetchall():
        mesma = next((t for t in tarefas if _mesmo_ambiente(t["ambiente"], m["ambiente"])), None)
        # Só "Geral" é tratado como sem ambiente (uma foto em "Área Externa" não vai para o banheiro).
        alvo = mesma or (tarefas[0] if normalizar_busca(m["ambiente"]) in ("", "geral", "obra") else None)
        if alvo is None:
            continue
        titulo = m["titulo_servico"]
        if not titulo or titulo == "Registro fotográfico do dia":
            titulo = alvo["descricao"][:120]
        conn.execute(
            "UPDATE midias_obra SET tarefa_id = ?, ambiente = ?, titulo_servico = ? WHERE id = ?",
            (alvo["id"], alvo["ambiente"] if mesma is None else m["ambiente"], titulo, m["id"]),
        )
        alteradas += 1
    return alteradas


def vincular_fotos_soltas_do_registro(registro_id: int) -> int:
    with get_connection() as conn:
        return _vincular_fotos_soltas(conn, registro_id)


def aplicar_reprocessamento_registro(
    registro_id: int,
    transcricao_audio: str,
    resumo_executivo: str,
    detalhes_execucao_imprevistos: str,
    motivo_atraso: str,
    tarefas: list[dict],
    mapeamento_asbuilt: Optional[str] = None,
) -> dict:
    """Grava a nova análise de um registro já salvo (ex.: áudio transcrito de novo).

    Atualiza os textos do dia e faz "upsert" das tarefas por ambiente: a tarefa do registro
    no mesmo ambiente é atualizada (título, resumo e detalhes; o status marcado à mão é
    mantido); ambientes novos viram tarefas novas. Tarefas antigas sem correspondente ficam.
    Por fim, liga as fotos soltas do registro às tarefas. Retorna contagens.
    """
    with get_connection() as conn:
        reg = conn.execute("SELECT obra_id, mapeamento_asbuilt FROM registros_diarios WHERE id = ?",
                           (registro_id,)).fetchone()
        if reg is None:
            raise ValueError(f"Registro inexistente: {registro_id}")
        asbuilt = reg["mapeamento_asbuilt"]
        if mapeamento_asbuilt and (not (asbuilt or "").strip() or (asbuilt or "").strip() == SEM_FOTO_ASBUILT):
            asbuilt = mapeamento_asbuilt
        conn.execute(
            """UPDATE registros_diarios SET transcricao_audio = ?, resumo_executivo = ?,
                      detalhes_execucao_imprevistos = ?, motivo_atraso = ?, mapeamento_asbuilt = ?
               WHERE id = ?""",
            (transcricao_audio, resumo_executivo, detalhes_execucao_imprevistos, motivo_atraso, asbuilt, registro_id),
        )
        existentes = conn.execute("SELECT id, ambiente FROM tarefas_obra WHERE registro_id = ? ORDER BY id",
                                  (registro_id,)).fetchall()
        usados: set[int] = set()
        criadas = atualizadas = 0
        for t in tarefas:
            desc = (t.get("descricao") or "").strip()
            if not desc:
                continue
            ambiente = _normalizar_ambiente(t.get("ambiente"))
            alvo = next((e for e in existentes if e["id"] not in usados
                         and normalizar_busca(e["ambiente"]) == normalizar_busca(ambiente)), None)
            if alvo:
                usados.add(alvo["id"])
                conn.execute(
                    "UPDATE tarefas_obra SET descricao = ?, resumo_servico = ?, detalhes_servico = ? WHERE id = ?",
                    (desc, t.get("resumo_servico") or "", t.get("detalhes_servico") or "", alvo["id"]),
                )
                atualizadas += 1
            else:
                conn.execute(
                    """INSERT INTO tarefas_obra (obra_id, registro_id, ambiente, descricao, concluida, data_criacao,
                                                 resumo_servico, detalhes_servico)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (reg["obra_id"], registro_id, ambiente, desc, 1 if t.get("concluida") else 0,
                     datetime.now().isoformat(timespec="seconds"),
                     t.get("resumo_servico") or "", t.get("detalhes_servico") or ""),
                )
                criadas += 1
        fotos = _vincular_fotos_soltas(conn, registro_id)
    return {"criadas": criadas, "atualizadas": atualizadas, "fotos_vinculadas": fotos}


def excluir_registro_diario(registro_id: int, excluir_tarefas: bool = False, excluir_fotos: bool = False) -> bool:
    """Exclui um registro diário e apaga com segurança suas mídias órfãs em uploads\\.

    Tarefas e fotos da galeria vindas desse registro são excluídas (`excluir_tarefas` /
    `excluir_fotos`) ou mantidas na obra, desvinculadas (padrão). O áudio é sempre apagado.
    Retorna False se o registro não existir.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT caminho_foto, caminho_audio FROM registros_diarios WHERE id = ?", (registro_id,)
        ).fetchone()
        if row is None:
            return False
        caminhos = [row["caminho_foto"], row["caminho_audio"]]
        if excluir_fotos:
            caminhos += [m["caminho_foto"] for m in conn.execute(
                "SELECT caminho_foto FROM midias_obra WHERE registro_id = ?", (registro_id,))]
            conn.execute("DELETE FROM midias_obra WHERE registro_id = ?", (registro_id,))
        else:
            conn.execute("UPDATE midias_obra SET registro_id = NULL WHERE registro_id = ?", (registro_id,))
        if excluir_tarefas:
            conn.execute("UPDATE midias_obra SET tarefa_id = NULL WHERE tarefa_id IN "
                         "(SELECT id FROM tarefas_obra WHERE registro_id = ?)", (registro_id,))
            conn.execute("DELETE FROM tarefas_obra WHERE registro_id = ?", (registro_id,))
        else:
            conn.execute("UPDATE tarefas_obra SET registro_id = NULL WHERE registro_id = ?", (registro_id,))
        conn.execute("DELETE FROM registros_diarios WHERE id = ?", (registro_id,))
        _remover_midias_orfas(conn, caminhos)
        return True


# --------------------------------------------------------------------------- #
# Checklist de tarefas
# --------------------------------------------------------------------------- #
def _normalizar_ambiente(ambiente: Optional[str]) -> str:
    return (ambiente or "").strip() or "Geral"


def inserir_tarefa(
    obra_id: int,
    registro_id: Optional[int],
    ambiente: str,
    descricao: str,
    concluida: int | bool = 1,
    resumo_servico: str = "",
    detalhes_servico: str = "",
) -> int:
    """Insere uma tarefa no checklist da obra e retorna o seu id.

    `resumo_servico`/`detalhes_servico`: o que foi dito no relato SÓ sobre este serviço.
    """
    if not descricao or not descricao.strip():
        raise ValueError("A descrição da tarefa é obrigatória.")
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO tarefas_obra (obra_id, registro_id, ambiente, descricao, concluida, data_criacao,
                                      resumo_servico, detalhes_servico)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obra_id,
                registro_id,
                _normalizar_ambiente(ambiente),
                descricao.strip(),
                1 if concluida else 0,
                datetime.now().isoformat(timespec="seconds"),
                (resumo_servico or "").strip(),
                (detalhes_servico or "").strip(),
            ),
        )
        return int(cur.lastrowid)


def listar_tarefas_obra(obra_id: int) -> list[dict]:
    """Tarefas da obra ordenadas por ambiente; pendentes antes das concluídas."""
    return buscar_tarefas(obra_id)


def buscar_tarefas(
    obra_id: int,
    busca: str = "",
    ambientes: Optional[list[str]] = None,
    status: str = "Todas",
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    apenas_atraso: bool = False,
    texto_sem_atraso: str = "",
) -> list[dict]:
    """Tarefas da obra que atendem a todos os filtros (por ambiente; pendentes primeiro).

    A data de uma tarefa é a do registro de origem ou, se foi digitada à mão, a de criação.
    O filtro de atraso pertence ao registro: com ele ativo, só entram tarefas extraídas
    de registros que o atendem.
    """
    if status not in STATUS_TAREFA:
        raise ValueError(f"Status de tarefa inválido: {status!r}. Use um de {STATUS_TAREFA}.")
    where, params = ["t.obra_id = ?"], [obra_id]
    # Cada palavra pode estar na tarefa, no relato do dia que a originou (transcrição/resumo)
    # ou no título do serviço das fotos vinculadas a ela.
    campos_busca = ("t.descricao", "t.ambiente", "t.resumo_servico", "r.transcricao_audio", "r.resumo_executivo")
    for termo in _termos(busca):
        campos = " OR ".join(f"norm({c}) LIKE ? ESCAPE '\\'" for c in campos_busca)
        where.append(
            f"""({campos} OR EXISTS (
                    SELECT 1 FROM midias_obra m WHERE m.tarefa_id = t.id
                    AND norm(m.titulo_servico) LIKE ? ESCAPE '\\'))"""
        )
        params += [termo] * (len(campos_busca) + 1)
    if ambientes:
        where.append(f"t.ambiente IN ({', '.join('?' * len(ambientes))})")
        params += list(ambientes)
    if status != "Todas":
        where.append("t.concluida = ?")
        params.append(1 if status == "Concluídas" else 0)
    data_tarefa = "COALESCE(r.data_registro, substr(t.data_criacao, 1, 10))"
    if data_inicio:
        where.append(f"{data_tarefa} >= ?")
        params.append(data_inicio)
    if data_fim:
        where.append(f"{data_tarefa} <= ?")
        params.append(data_fim)
    if apenas_atraso:
        sql, p = _filtro_atraso(texto_sem_atraso)
        where.append(f"r.id IS NOT NULL AND {sql}")
        params += p
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT t.id, t.obra_id, t.registro_id, t.ambiente, t.descricao, t.concluida, t.data_criacao,
                   t.resumo_servico, t.detalhes_servico
            FROM tarefas_obra t
            LEFT JOIN registros_diarios r ON r.id = t.registro_id
            WHERE {' AND '.join(where)}
            ORDER BY t.ambiente COLLATE NOCASE, t.concluida, t.id
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


_COLUNAS_DOSSIE_REGISTRO = (
    "data_registro", "transcricao_audio", "resumo_executivo", "motivo_atraso",
    "detalhes_execucao_imprevistos", "mapeamento_asbuilt", "caminho_audio",
)
_COLUNAS_DOSSIE_MIDIA = (
    "id", "registro_id", "tarefa_id", "ambiente", "titulo_servico", "descricao_tecnica",
    "caminho_foto", "data_midia", "foto_bytes_original",
)


def _cond_midia_da_tarefa(m: str) -> str:
    """Fotos que documentam a tarefa `b`: vinculadas a ela ou, se soltas (sem tarefa), do
    mesmo ambiente e do mesmo registro/dia. Fotos de OUTRA tarefa nunca entram, nem fotos soltas
    que a IA já analisou e não ligou a nenhuma tarefa (vinculo_ia = 1: ficam só no registro do cômodo)."""
    return (
        f"{m}.obra_id = b.obra_id AND ({m}.tarefa_id = b.id OR ({m}.tarefa_id IS NULL AND {m}.vinculo_ia IS NULL "
        f"AND {m}.ambiente = b.ambiente AND ({m}.registro_id = b.reg_id OR {m}.data_midia = b.data_tarefa)))"
    )


def buscar_dossie_tarefas(
    obra_id: int,
    termo_busca: str = "",
    ambiente_filtro: str | list[str] | None = "Todos",
    status_filtro: str = "Todos",
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    apenas_atraso: bool = False,
    texto_sem_atraso: str = "",
    priorizar_diretos: bool = True,
) -> list[dict]:
    """Dossiê completo de cada tarefa: a tarefa, o registro diário do dia e as fotos do serviço.

    - Registro: o de origem (`registro_id`) ou, para tarefas digitadas à mão, o registro da
      mesma obra na data de criação da tarefa (LEFT JOIN: pode não haver).
    - Fotos (LEFT JOIN): as vinculadas à tarefa e as soltas do mesmo ambiente e registro/dia.
    - `termo_busca`: cada palavra precisa aparecer na tarefa, no relato/resumo/detalhes/atraso
      do dia ou no título/descrição de alguma foto do dossiê (sem acentos/maiúsculas). Com
      `priorizar_diretos`, se alguma tarefa corresponde pelo próprio texto/fotos, as que só
      batem pelo relato do dia ficam de fora.
    - `ambiente_filtro`: "Todos", um ambiente ou uma lista; `status_filtro`: "Todos"/"Todas",
      "Pendentes" ou "Concluídas".

    Retorna dicts com os campos da tarefa, `data_registro_origem` (dia do relato ou da
    criação), `data_execucao` (só concluídas), `registro` (dict ou None) e `fotos` (lista),
    do dia mais recente para o mais antigo.
    """
    status = "Todas" if status_filtro in (None, "", "Todos") else status_filtro
    if status not in STATUS_TAREFA:
        raise ValueError(f"Status de tarefa inválido: {status_filtro!r}.")
    if isinstance(ambiente_filtro, str):
        ambientes = [] if ambiente_filtro in ("", "Todos", "Todos os ambientes") else [ambiente_filtro]
    else:
        ambientes = list(ambiente_filtro or [])

    where, params = [], []
    textos = ("b.descricao", "b.ambiente", "b.resumo_servico", "b.detalhes_servico", "r.transcricao_audio",
              "r.resumo_executivo", "r.motivo_atraso", "r.detalhes_execucao_imprevistos")
    for termo in _termos(termo_busca):
        campos = " OR ".join(f"norm({c}) LIKE ? ESCAPE '\\'" for c in textos)
        where.append(
            f"""({campos} OR EXISTS (
                    SELECT 1 FROM midias_obra m2 WHERE {_cond_midia_da_tarefa('m2')}
                    AND (norm(m2.titulo_servico) LIKE ? ESCAPE '\\' OR norm(m2.descricao_tecnica) LIKE ? ESCAPE '\\')))"""
        )
        params += [termo] * (len(textos) + 2)
    if ambientes:
        where.append(f"b.ambiente IN ({', '.join('?' * len(ambientes))})")
        params += ambientes
    if status != "Todas":
        where.append("b.concluida = ?")
        params.append(1 if status == "Concluídas" else 0)
    if data_inicio:
        where.append("b.data_tarefa >= ?")
        params.append(data_inicio)
    if data_fim:
        where.append("b.data_tarefa <= ?")
        params.append(data_fim)
    if apenas_atraso:
        sql, p = _filtro_atraso(texto_sem_atraso)
        where.append(f"r.id IS NOT NULL AND {sql}")
        params += p

    col_r = ", ".join(f"r.{c} AS r_{c}" for c in _COLUNAS_DOSSIE_REGISTRO)
    col_m = ", ".join(f"m.{c} AS m_{c}" for c in _COLUNAS_DOSSIE_MIDIA)
    sql = f"""
        WITH b AS (
            SELECT t.id, t.obra_id, t.registro_id, t.ambiente, t.descricao, t.concluida, t.data_criacao,
                   t.data_conclusao, t.resumo_servico, t.detalhes_servico,
                   COALESCE(t.registro_id, (
                       SELECT r2.id FROM registros_diarios r2
                       WHERE r2.obra_id = t.obra_id AND r2.data_registro = substr(t.data_criacao, 1, 10)
                       ORDER BY r2.id DESC LIMIT 1)) AS reg_id,
                   COALESCE(r0.data_registro, substr(t.data_criacao, 1, 10)) AS data_tarefa
            FROM tarefas_obra t
            LEFT JOIN registros_diarios r0 ON r0.id = t.registro_id
            WHERE t.obra_id = ?
        )
        SELECT b.*, r.id AS r_id, {col_r}, {col_m}
        FROM b
        LEFT JOIN registros_diarios r ON r.id = b.reg_id
        LEFT JOIN midias_obra m ON {_cond_midia_da_tarefa('m')}
        {('WHERE ' + ' AND '.join(where)) if where else ''}
        ORDER BY b.data_tarefa DESC, b.id DESC, m.data_midia, m.id
    """
    with get_connection() as conn:
        rows = conn.execute(sql, [obra_id] + params).fetchall()

    dossie: dict[int, dict] = {}
    for row in rows:
        item = dossie.get(row["id"])
        if item is None:
            item = dossie[row["id"]] = {
                "id": row["id"], "obra_id": row["obra_id"], "registro_id": row["registro_id"],
                "ambiente": row["ambiente"], "descricao": row["descricao"], "concluida": row["concluida"],
                "resumo_servico": row["resumo_servico"] or "", "detalhes_servico": row["detalhes_servico"] or "",
                "data_criacao": row["data_criacao"], "data_registro_origem": row["data_tarefa"],
                # Concluída: o dia em que foi marcada como feita ou, se já nasceu feita, o do registro.
                "data_execucao": (row["data_conclusao"] or row["data_tarefa"]) if row["concluida"] else None,
                "registro": (
                    {"id": row["r_id"], **{c: row[f"r_{c}"] for c in _COLUNAS_DOSSIE_REGISTRO}}
                    if row["r_id"] is not None else None
                ),
                "fotos": [],
            }
        if row["m_id"] is not None:
            item["fotos"].append({c: row[f"m_{c}"] for c in _COLUNAS_DOSSIE_MIDIA})
    resultado = list(dossie.values())
    if termo_busca and priorizar_diretos:
        # "vala de infiltração" deve trazer a tarefa da vala, não todas as do mesmo relato:
        # se alguma tarefa corresponde pelo próprio texto/fotos, só elas entram.
        diretos = [
            d for d in resultado
            if corresponde_busca(termo_busca, d["descricao"], d["ambiente"], d["resumo_servico"], d["detalhes_servico"],
                                 *(f"{f['titulo_servico'] or ''} {f['descricao_tecnica'] or ''}" for f in d["fotos"]))
        ]
        if diretos:
            return diretos
    return resultado


def listar_ambientes_recomendados(obra_id: Optional[int] = None) -> list[str]:
    """Ambientes sugeridos, aprendendo com o histórico de tarefas.

    Ordem: ambientes já usados nesta obra (mais frequentes primeiro), depois os usados
    nas demais obras (aprendidos dos áudios ou digitados à mão) e, por fim, os ambientes
    base. Sem duplicatas, ignorando maiúsculas e acentos.
    """
    usados = "SELECT obra_id, ambiente FROM tarefas_obra UNION ALL SELECT obra_id, ambiente FROM midias_obra"
    with get_connection() as conn:
        globais = conn.execute(
            f"SELECT ambiente, COUNT(*) AS freq FROM ({usados}) GROUP BY ambiente ORDER BY freq DESC, ambiente"
        ).fetchall()
        da_obra = (
            conn.execute(
                f"SELECT ambiente, COUNT(*) AS freq FROM ({usados}) WHERE obra_id = ? "
                "GROUP BY ambiente ORDER BY freq DESC, ambiente",
                (obra_id,),
            ).fetchall()
            if obra_id is not None
            else []
        )
    recomendados: dict[str, str] = {}
    for nome in [r["ambiente"] for r in da_obra] + [r["ambiente"] for r in globais] + list(AMBIENTES_BASE):
        nome = (nome or "").strip()
        if nome and normalizar_busca(nome) not in recomendados:
            recomendados[normalizar_busca(nome)] = nome
    return list(recomendados.values())


def alternar_status_tarefa(tarefa_id: int, concluida: int | bool, data_conclusao: Optional[str] = None) -> bool:
    """Marca a tarefa como concluída (1, registrando o dia) ou pendente (0). False se não existir."""
    feita = 1 if concluida else 0
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE tarefas_obra SET concluida = ?, data_conclusao = ? WHERE id = ?",
            (feita, (data_conclusao or date.today().isoformat()) if feita else None, tarefa_id),
        )
        return cur.rowcount > 0


def editar_tarefa(tarefa_id: int, ambiente: str, descricao: str,
                  resumo_servico: Optional[str] = None, detalhes_servico: Optional[str] = None) -> bool:
    """Altera ambiente e descrição de uma tarefa. False se não existir.

    As fotos vinculadas à tarefa que estavam no ambiente antigo acompanham a mudança
    (ex.: tarefa corrigida de "Área Externa" para "Banheiro" leva suas fotos junto).
    """
    if not descricao or not descricao.strip():
        raise ValueError("A descrição da tarefa é obrigatória.")
    novo = _normalizar_ambiente(ambiente)
    with get_connection() as conn:
        antigo = conn.execute("SELECT ambiente FROM tarefas_obra WHERE id = ?", (tarefa_id,)).fetchone()
        if antigo is None:
            return False
        conn.execute(
            "UPDATE tarefas_obra SET ambiente = ?, descricao = ?, resumo_servico = COALESCE(?, resumo_servico), "
            "detalhes_servico = COALESCE(?, detalhes_servico) WHERE id = ?",
            (novo, descricao.strip(), resumo_servico, detalhes_servico, tarefa_id),
        )
        conn.execute(
            "UPDATE midias_obra SET ambiente = ? WHERE tarefa_id = ? AND ambiente = ?",
            (novo, tarefa_id, antigo["ambiente"]),
        )
        return True


def excluir_tarefa(tarefa_id: int) -> bool:
    """Exclui uma tarefa do checklist (as fotos dela continuam na galeria). False se não existir."""
    with get_connection() as conn:
        conn.execute("UPDATE midias_obra SET tarefa_id = NULL WHERE tarefa_id = ?", (tarefa_id,))
        cur = conn.execute("DELETE FROM tarefas_obra WHERE id = ?", (tarefa_id,))
        return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Fotos da obra (galeria por ambiente e serviço)
# --------------------------------------------------------------------------- #
def inserir_midia_obra(
    obra_id: int,
    caminho_foto: str | Path,
    ambiente: str,
    titulo_servico: str = "",
    descricao_tecnica: str = "",
    registro_id: Optional[int] = None,
    tarefa_id: Optional[int] = None,
    data_midia: Optional[str] = None,
    foto_bytes_original: Optional[int] = None,
    vinculo_ia: bool = False,
) -> int:
    """Vincula uma foto (já salva em uploads\\) à obra, a um ambiente e, opcionalmente, a um
    registro diário e/ou tarefa. Retorna o id da mídia.

    `vinculo_ia=True`: o vínculo (ou a ausência de tarefa) foi decidido pela análise visual da
    IA, e a foto solta não é religada automaticamente por ambiente depois."""
    if caminho_midia_seguro(caminho_foto) is None:
        raise CaminhoInseguroError(f"Caminho de foto fora da pasta uploads: {caminho_foto}")
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO midias_obra (obra_id, registro_id, tarefa_id, ambiente, titulo_servico,
                                     descricao_tecnica, caminho_foto, data_midia, foto_bytes_original, vinculo_ia)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obra_id, registro_id, tarefa_id, _normalizar_ambiente(ambiente),
                (titulo_servico or "").strip(), (descricao_tecnica or "").strip(),
                str(Path(caminho_foto)), data_midia or date.today().isoformat(), foto_bytes_original,
                1 if vinculo_ia else None,
            ),
        )
        return int(cur.lastrowid)


def listar_midias_obra(obra_id: int, registro_id: Optional[int] = None, tarefa_id: Optional[int] = None) -> list[dict]:
    """Fotos da obra (opcionalmente só de um registro ou tarefa), por ambiente e data.

    Inclui dados da tarefa e do registro vinculados, para busca e exibição.
    """
    where, params = ["m.obra_id = ?"], [obra_id]
    if registro_id is not None:
        where.append("m.registro_id = ?")
        params.append(registro_id)
    if tarefa_id is not None:
        where.append("m.tarefa_id = ?")
        params.append(tarefa_id)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT m.id, m.obra_id, m.registro_id, m.tarefa_id, m.ambiente, m.titulo_servico,
                   m.descricao_tecnica, m.caminho_foto, m.data_midia, m.foto_bytes_original,
                   t.descricao AS tarefa_descricao, t.concluida AS tarefa_concluida,
                   r.transcricao_audio, r.resumo_executivo, r.motivo_atraso, r.detalhes_execucao_imprevistos
            FROM midias_obra m
            LEFT JOIN tarefas_obra t ON t.id = m.tarefa_id
            LEFT JOIN registros_diarios r ON r.id = m.registro_id
            WHERE {' AND '.join(where)}
            ORDER BY m.ambiente COLLATE NOCASE, m.data_midia DESC, m.id
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def editar_midia_obra(midia_id: int, ambiente: str, titulo_servico: str, descricao_tecnica: str) -> bool:
    """Altera ambiente, título do serviço e descrição técnica de uma foto. False se não existir."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE midias_obra SET ambiente = ?, titulo_servico = ?, descricao_tecnica = ? WHERE id = ?",
            (_normalizar_ambiente(ambiente), (titulo_servico or "").strip(), (descricao_tecnica or "").strip(), midia_id),
        )
        return cur.rowcount > 0


def excluir_midia_obra(midia_id: int) -> bool:
    """Exclui a foto da galeria e apaga o arquivo de uploads\\ se nada mais o usar.

    Para fotos migradas de registros antigos, também desvincula a foto do registro
    (senão a migração a recolocaria na galeria no próximo início).
    """
    with get_connection() as conn:
        row = conn.execute("SELECT caminho_foto FROM midias_obra WHERE id = ?", (midia_id,)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM midias_obra WHERE id = ?", (midia_id,))
        conn.execute(
            "UPDATE registros_diarios SET caminho_foto = NULL, foto_bytes_original = NULL WHERE caminho_foto = ?",
            (row["caminho_foto"],),
        )
        _remover_midias_orfas(conn, [row["caminho_foto"]])
        return True


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #
def seed_data() -> None:
    """Insere obras de exemplo apenas se a tabela estiver vazia."""
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM clientes_obras").fetchone()[0]
    if total:
        return

    criar_obra(
        nome_cliente="Reforma Banheiro - Dona Maria",
        endereco="Rua das Flores, 123 - Centro",
        escopo_resumo="Troca de revestimento, substituição da tubulação hidráulica e instalação de box.",
        status="Em Andamento",
    )
    criar_obra(
        nome_cliente="Muro e Calçada - Sr. João",
        endereco="Av. Brasil, 456 - Jardim América",
        escopo_resumo="Construção de muro de alvenaria (15 m) e calçada em concreto (20 m²).",
        status="Em Andamento",
    )


if __name__ == "__main__":
    init_db()
    seed_data()
    for obra in listar_obras():
        print(f"[{obra['id']}] {obra['nome_cliente']} - {obra['status']}")
