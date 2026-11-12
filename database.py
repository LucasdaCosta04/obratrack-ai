"""Camada de persistência SQLite do ObraTrack AI."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator, Optional

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "obratrack.db"
UPLOADS_DIR = BASE_DIR / "uploads"

STATUS_OBRA = ("Em Andamento", "Pausada", "Concluída")
NIVEIS_PRODUTIVIDADE = ("Baixa", "Média", "Alta")


@contextmanager
def get_connection(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Abre uma conexão com FK habilitada, commit automático e fechamento garantido."""
    conn = sqlite3.connect(str(db_path))
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
    """Cria as tabelas (se não existirem) e a pasta de uploads."""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
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
                FOREIGN KEY (obra_id) REFERENCES clientes_obras (id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_registros_obra_id
                ON registros_diarios (obra_id);
            """
        )


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
    data_registro: Optional[str] = None,
) -> int:
    """Insere um registro diário vinculado a uma obra e retorna o seu id."""
    if nivel_produtividade is not None and nivel_produtividade not in NIVEIS_PRODUTIVIDADE:
        raise ValueError(
            f"Nível de produtividade inválido: {nivel_produtividade!r}. "
            f"Use um de {NIVEIS_PRODUTIVIDADE}."
        )
    data_registro = data_registro or date.today().isoformat()
    foto = str(Path(caminho_foto)) if caminho_foto else None
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO registros_diarios (
                obra_id, data_registro, transcricao_audio, resumo_executivo,
                nivel_produtividade, motivo_atraso, mapeamento_asbuilt, caminho_foto
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        return int(cur.lastrowid)


def listar_registros_por_obra(obra_id: int) -> list[dict]:
    """Retorna os registros diários de uma obra, do mais recente ao mais antigo."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM registros_diarios
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
