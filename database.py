"""Camada de persistência SQLite do ObraTrack AI."""

from __future__ import annotations

import os
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
NIVEIS_PRODUTIVIDADE = ("Baixa", "Média", "Alta")

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
                nivel_produtividade  TEXT
                                     CHECK (nivel_produtividade IN ('Baixa', 'Média', 'Alta')),
                motivo_atraso        TEXT,
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
                FOREIGN KEY (obra_id) REFERENCES clientes_obras (id) ON DELETE CASCADE,
                FOREIGN KEY (registro_id) REFERENCES registros_diarios (id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tarefas_obra_id ON tarefas_obra (obra_id);
            CREATE INDEX IF NOT EXISTS idx_tarefas_registro_id ON tarefas_obra (registro_id);
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
    _sanear_textos_legados(conn)


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


def _validar_nivel(nivel: Optional[str]) -> None:
    if nivel is not None and nivel not in NIVEIS_PRODUTIVIDADE:
        raise ValueError(f"Nível de produtividade inválido: {nivel!r}. Use um de {NIVEIS_PRODUTIVIDADE}.")


def _remover_midias_orfas(conn: sqlite3.Connection, caminhos: list[Optional[str]]) -> int:
    """Apaga do disco as mídias que nenhum registro referencia mais.

    Só remove arquivos dentro de uploads\\ (caminho_midia_seguro). Deve ser chamada
    depois do DELETE, na mesma conexão, para enxergar o estado já atualizado.
    """
    removidos = 0
    for caminho in {c for c in caminhos if c}:
        ainda_usado = conn.execute(
            "SELECT 1 FROM registros_diarios WHERE caminho_foto = ? OR caminho_audio = ? LIMIT 1",
            (caminho, caminho),
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
        ]
        # Exclusão explícita (não depende de ON DELETE CASCADE em bancos antigos).
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
    nivel_produtividade: Optional[str] = None,
    motivo_atraso: str = "",
    mapeamento_asbuilt: str = "",
    caminho_foto: Optional[str | Path] = None,
    caminho_audio: Optional[str | Path] = None,
    data_registro: Optional[str] = None,
    foto_bytes_original: Optional[int] = None,
) -> int:
    """Insere um registro diário vinculado a uma obra e retorna o seu id."""
    _validar_nivel(nivel_produtividade)
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
                nivel_produtividade, motivo_atraso, mapeamento_asbuilt,
                caminho_foto, caminho_audio, foto_bytes_original
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obra_id,
                data_registro,
                transcricao_audio,
                resumo_executivo,
                nivel_produtividade,
                motivo_atraso,
                mapeamento_asbuilt,
                foto,
                audio,
                foto_bytes_original if foto else None,
            ),
        )
        return int(cur.lastrowid)


_COLUNAS_REGISTRO = """r.id, r.obra_id, r.data_registro, r.transcricao_audio, r.resumo_executivo,
                   r.nivel_produtividade, r.motivo_atraso, r.mapeamento_asbuilt,
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
    niveis: Optional[list[str]] = None,
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
    textos = ("r.transcricao_audio", "r.resumo_executivo", "r.motivo_atraso", "r.mapeamento_asbuilt")
    for termo in _termos(busca):
        campos = " OR ".join(f"norm({c}) LIKE ? ESCAPE '\\'" for c in textos)
        where.append(
            f"""({campos} OR EXISTS (
                    SELECT 1 FROM tarefas_obra t WHERE t.registro_id = r.id
                    AND (norm(t.descricao) LIKE ? ESCAPE '\\' OR norm(t.ambiente) LIKE ? ESCAPE '\\')))"""
        )
        params += [termo] * (len(textos) + 2)
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
    if niveis:
        where.append(f"r.nivel_produtividade IN ({', '.join('?' * len(niveis))})")
        params += list(niveis)
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
    nivel_produtividade: str,
    motivo_atraso: str,
    mapeamento_asbuilt: str,
) -> bool:
    """Atualiza os campos textuais de um registro (mídias não mudam). False se não existir."""
    _validar_nivel(nivel_produtividade)
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE registros_diarios
            SET data_registro = ?, transcricao_audio = ?, resumo_executivo = ?,
                nivel_produtividade = ?, motivo_atraso = ?, mapeamento_asbuilt = ?
            WHERE id = ?
            """,
            (
                data_registro,
                transcricao_audio,
                resumo_executivo,
                nivel_produtividade,
                motivo_atraso,
                mapeamento_asbuilt,
                registro_id,
            ),
        )
        return cur.rowcount > 0


def excluir_registro_diario(registro_id: int, excluir_tarefas: bool = False) -> bool:
    """Exclui um registro diário e apaga com segurança suas mídias órfãs em uploads\\.

    Tarefas extraídas desse registro são excluídas (`excluir_tarefas=True`) ou mantidas
    no checklist, desvinculadas (padrão). Retorna False se o registro não existir.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT caminho_foto, caminho_audio FROM registros_diarios WHERE id = ?", (registro_id,)
        ).fetchone()
        if row is None:
            return False
        if excluir_tarefas:
            conn.execute("DELETE FROM tarefas_obra WHERE registro_id = ?", (registro_id,))
        else:
            conn.execute("UPDATE tarefas_obra SET registro_id = NULL WHERE registro_id = ?", (registro_id,))
        conn.execute("DELETE FROM registros_diarios WHERE id = ?", (registro_id,))
        _remover_midias_orfas(conn, [row["caminho_foto"], row["caminho_audio"]])
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
) -> int:
    """Insere uma tarefa no checklist da obra e retorna o seu id."""
    if not descricao or not descricao.strip():
        raise ValueError("A descrição da tarefa é obrigatória.")
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO tarefas_obra (obra_id, registro_id, ambiente, descricao, concluida, data_criacao)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                obra_id,
                registro_id,
                _normalizar_ambiente(ambiente),
                descricao.strip(),
                1 if concluida else 0,
                datetime.now().isoformat(timespec="seconds"),
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
    niveis: Optional[list[str]] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    apenas_atraso: bool = False,
    texto_sem_atraso: str = "",
) -> list[dict]:
    """Tarefas da obra que atendem a todos os filtros (por ambiente; pendentes primeiro).

    A data de uma tarefa é a do registro de origem ou, se foi digitada à mão, a de criação.
    Filtros de produtividade e atraso pertencem ao registro: com eles ativos, só entram
    tarefas extraídas de registros que os atendem.
    """
    if status not in STATUS_TAREFA:
        raise ValueError(f"Status de tarefa inválido: {status!r}. Use um de {STATUS_TAREFA}.")
    where, params = ["t.obra_id = ?"], [obra_id]
    for termo in _termos(busca):
        where.append("(norm(t.descricao) LIKE ? ESCAPE '\\' OR norm(t.ambiente) LIKE ? ESCAPE '\\')")
        params += [termo, termo]
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
    if niveis:
        where.append(f"r.nivel_produtividade IN ({', '.join('?' * len(niveis))})")
        params += list(niveis)
    if apenas_atraso:
        sql, p = _filtro_atraso(texto_sem_atraso)
        where.append(f"r.id IS NOT NULL AND {sql}")
        params += p
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT t.id, t.obra_id, t.registro_id, t.ambiente, t.descricao, t.concluida, t.data_criacao
            FROM tarefas_obra t
            LEFT JOIN registros_diarios r ON r.id = t.registro_id
            WHERE {' AND '.join(where)}
            ORDER BY t.ambiente COLLATE NOCASE, t.concluida, t.id
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def listar_ambientes_recomendados(obra_id: Optional[int] = None) -> list[str]:
    """Ambientes sugeridos, aprendendo com o histórico de tarefas.

    Ordem: ambientes já usados nesta obra (mais frequentes primeiro), depois os usados
    nas demais obras (aprendidos dos áudios ou digitados à mão) e, por fim, os ambientes
    base. Sem duplicatas, ignorando maiúsculas e acentos.
    """
    with get_connection() as conn:
        globais = conn.execute(
            "SELECT ambiente, COUNT(*) AS freq FROM tarefas_obra GROUP BY ambiente ORDER BY freq DESC, ambiente"
        ).fetchall()
        da_obra = (
            conn.execute(
                "SELECT ambiente, COUNT(*) AS freq FROM tarefas_obra WHERE obra_id = ? "
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


def alternar_status_tarefa(tarefa_id: int, concluida: int | bool) -> bool:
    """Marca a tarefa como concluída (1) ou pendente (0). False se não existir."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE tarefas_obra SET concluida = ? WHERE id = ?", (1 if concluida else 0, tarefa_id)
        )
        return cur.rowcount > 0


def editar_tarefa(tarefa_id: int, ambiente: str, descricao: str) -> bool:
    """Altera ambiente e descrição de uma tarefa. False se não existir."""
    if not descricao or not descricao.strip():
        raise ValueError("A descrição da tarefa é obrigatória.")
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE tarefas_obra SET ambiente = ?, descricao = ? WHERE id = ?",
            (_normalizar_ambiente(ambiente), descricao.strip(), tarefa_id),
        )
        return cur.rowcount > 0


def excluir_tarefa(tarefa_id: int) -> bool:
    """Exclui uma tarefa do checklist. False se não existir."""
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM tarefas_obra WHERE id = ?", (tarefa_id,))
        return cur.rowcount > 0


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
