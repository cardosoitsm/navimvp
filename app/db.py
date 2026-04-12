from contextlib import contextmanager
import time

import psycopg2
from psycopg2 import OperationalError

from app.config import get_settings

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS usuarios (
        id BIGSERIAL PRIMARY KEY,
        email VARCHAR(255) NOT NULL UNIQUE,
        senha VARCHAR(255) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transacoes (
        id BIGSERIAL PRIMARY KEY,
        tipo VARCHAR(20) NOT NULL CHECK (tipo IN ('receita', 'despesa')),
        categoria VARCHAR(100) NOT NULL,
        valor NUMERIC(12, 2) NOT NULL CHECK (valor > 0),
        user_id BIGINT NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_transacoes_user_id ON transacoes(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_transacoes_user_categoria ON transacoes(user_id, categoria)",
)


def get_connection():
    settings = get_settings()
    return psycopg2.connect(
        host=settings.database_host,
        database=settings.database_name,
        user=settings.database_user,
        password=settings.database_password,
        port=settings.database_port,
    )


@contextmanager
def get_cursor():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        yield conn, cursor
    finally:
        cursor.close()
        conn.close()


def init_db(max_attempts: int = 10, retry_delay: int = 2) -> None:
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            with get_cursor() as (conn, cursor):
                for statement in SCHEMA_STATEMENTS:
                    cursor.execute(statement)
                conn.commit()
            return
        except OperationalError as exc:
            last_error = exc
            if attempt == max_attempts:
                break
            time.sleep(retry_delay)

    if last_error is not None:
        raise last_error


def ping_db() -> bool:
    try:
        with get_cursor() as (_, cursor):
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True
    except Exception:
        return False
