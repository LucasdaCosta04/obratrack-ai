"""Camada de persistência SQLite do ObraTrack AI."""

from __future__ import annotations

import os
import sqlite3
import warnings
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator, Optional

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "obratrack.db"
UPLOADS_DIR = BASE_DIR / "uploads"
GITIGNORE_PATH = BASE_DIR / ".gitignore"

STATUS_OBRA = ("Em Andamento", "Pausada", "Concluída")
NIVEIS_PRODUTIVIDADE = ("Baixa", "Média", "Alta")

# Entradas que obrigatoriamente precisam estar no .gitignore (dados sensíveis).
GITIGNORE_OBRIGATORIO = ("*.db", ".env", "uploads/")


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


@contextmanager
def get_connection(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Abre uma conexão com FK habilitada, commit automático e fechamento garantido."""
    conn = sqlite3.connect(str(validar_caminho_local(db_path)))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
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
                FOREIGN KEY (obra_id) REFERENCES clientes_obras (id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_registros_obra_id
                ON registros_diarios (obra_id);
            """
        )
        _migrar(conn)
    _restringir_permissoes(DB_PATH, 0o600)


def _migrar(conn: sqlite3.Connection) -> None:
    """Migrações idempotentes para bancos criados por versões anteriores."""
    colunas = {row["name"] for row in conn.execute("PRAGMA table_info(registros_diarios)")}
    if "caminho_audio" not in colunas:
        conn.execute("ALTER TABLE registros_diarios ADD COLUMN caminho_audio TEXT")


def listar_obras() -> list[dict]:
    """Retorna todas as obras, das mais recentes para as mais antigas."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM clientes_obras ORDER BY data_inicio DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def criar_obra(
    nome_cliente: str,
    endereco: str = "",
    escopo_resumo: str = "",
    status: str = "Em Andamento",
    data_inicio: Optional[str] = None,
) -> int:
    """Cria uma nova obra e retorna o seu id."""
    if status not in STATUS_OBRA:
        raise ValueError(f"Status inválido: {status!r}. Use um de {STATUS_OBRA}.")
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
) -> int:
    """Insere um registro diário vinculado a uma obra e retorna o seu id."""
    if nivel_produtividade is not None and nivel_produtividade not in NIVEIS_PRODUTIVIDADE:
        raise ValueError(
            f"Nível de produtividade inválido: {nivel_produtividade!r}. "
            f"Use um de {NIVEIS_PRODUTIVIDADE}."
        )
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
                caminho_foto, caminho_audio
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        return int(cur.lastrowid)


def listar_registros_por_obra(obra_id: int) -> list[dict]:
    """Retorna os registros diários de uma obra, do mais recente ao mais antigo."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, obra_id, data_registro, transcricao_audio, resumo_executivo,
                   nivel_produtividade, motivo_atraso, mapeamento_asbuilt,
                   caminho_foto, caminho_audio
            FROM registros_diarios
            WHERE obra_id = ?
            ORDER BY data_registro DESC, id DESC
            """,
            (obra_id,),
        ).fetchall()
    return [dict(r) for r in rows]


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
