"""
Camada de acesso a dados (SQLite) para o bot de RH.

Tabelas:
- funcionarios: espelho local dos colaboradores que já passaram pelo bot (ou
  foram detectados como desligados). Guarda o vínculo entre o cadastro no IXC
  e a conta do Telegram, além do último status conhecido (para detectar a
  transição Ativo -> Inativo sem reprocessar o mesmo desligamento duas vezes).
- grupos: registro dos grupos do Telegram em que o bot foi adicionado,
  classificados manualmente (via comando /classificar) como "geral" ou
  vinculados a um "setor".
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot_rh.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS funcionarios (
    ixc_id INTEGER PRIMARY KEY,
    nome TEXT,
    telefone TEXT,
    setor TEXT,
    status_ixc TEXT NOT NULL DEFAULT 'Ativo',
    telegram_user_id INTEGER,
    criado_em TEXT NOT NULL,
    onboarded_em TEXT,
    offboarded_em TEXT
);

CREATE TABLE IF NOT EXISTS grupos (
    chat_id INTEGER PRIMARY KEY,
    titulo TEXT,
    tipo TEXT,          -- 'geral' | 'setor' | NULL (ainda não classificado)
    setor TEXT,         -- preenchido apenas quando tipo = 'setor'
    adicionado_em TEXT NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------- funcionarios ----------

def get_funcionario(ixc_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM funcionarios WHERE ixc_id = ?", (ixc_id,)).fetchone()
        return dict(row) if row else None


def get_funcionario_by_telegram_id(telegram_user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM funcionarios WHERE telegram_user_id = ?", (telegram_user_id,)
        ).fetchone()
        return dict(row) if row else None


def upsert_funcionario_ixc(ixc_id, nome, telefone, setor):
    """Cria (ou atualiza os dados cadastrais de) um funcionário a partir do
    que foi encontrado no IXC. Não mexe em telegram_user_id nem em status_ixc
    de um registro já existente - isso é papel de vincular_telegram() e
    marcar_status()."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO funcionarios (ixc_id, nome, telefone, setor, status_ixc, criado_em)
               VALUES (?, ?, ?, ?, 'Ativo', ?)
               ON CONFLICT(ixc_id) DO UPDATE SET
                   nome = excluded.nome,
                   telefone = excluded.telefone,
                   setor = excluded.setor""",
            (ixc_id, nome, telefone, setor, _now_iso()),
        )


def vincular_telegram(ixc_id, telegram_user_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE funcionarios SET telegram_user_id = ?, onboarded_em = ? WHERE ixc_id = ?",
            (telegram_user_id, _now_iso(), ixc_id),
        )


def marcar_status(ixc_id, status_ixc):
    with get_conn() as conn:
        conn.execute("UPDATE funcionarios SET status_ixc = ? WHERE ixc_id = ?", (status_ixc, ixc_id))


def marcar_offboarded(ixc_id):
    with get_conn() as conn:
        conn.execute("UPDATE funcionarios SET offboarded_em = ? WHERE ixc_id = ?", (_now_iso(), ixc_id))


# ---------- grupos ----------

def upsert_grupo(chat_id, titulo):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO grupos (chat_id, titulo, adicionado_em) VALUES (?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET titulo = excluded.titulo""",
            (chat_id, titulo, _now_iso()),
        )


def remover_grupo(chat_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM grupos WHERE chat_id = ?", (chat_id,))


def classificar_grupo(chat_id, tipo, setor=None):
    with get_conn() as conn:
        conn.execute("UPDATE grupos SET tipo = ?, setor = ? WHERE chat_id = ?", (tipo, setor, chat_id))


def listar_grupos_gerais():
    with get_conn() as conn:
        rows = conn.execute("SELECT chat_id FROM grupos WHERE tipo = 'geral'").fetchall()
        return [r["chat_id"] for r in rows]


def listar_grupos_setor(setor):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT chat_id FROM grupos WHERE tipo = 'setor' AND setor = ?", (setor,)
        ).fetchall()
        return [r["chat_id"] for r in rows]


def listar_todos_grupos():
    with get_conn() as conn:
        rows = conn.execute("SELECT chat_id FROM grupos").fetchall()
        return [r["chat_id"] for r in rows]


def listar_grupos_nao_classificados():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM grupos WHERE tipo IS NULL").fetchall()
        return [dict(r) for r in rows]
