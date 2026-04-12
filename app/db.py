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
    """
    CREATE TABLE IF NOT EXISTS confirmacoes_pendentes (
        user_id BIGINT PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
        payload_json TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS configuracoes_usuario (
        user_id BIGINT PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
        orcamento_onboarding_concluido BOOLEAN NOT NULL DEFAULT FALSE,
        documentos_onboarding_concluido BOOLEAN NOT NULL DEFAULT FALSE,
        aguardando_documento BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orcamentos (
        user_id BIGINT NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        categoria VARCHAR(100) NOT NULL,
        limite_mensal NUMERIC(12, 2) NOT NULL CHECK (limite_mensal > 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, categoria)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orcamento_alertas (
        user_id BIGINT NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        categoria VARCHAR(100) NOT NULL,
        mes_referencia DATE NOT NULL,
        nivel_alerta INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, categoria, mes_referencia, nivel_alerta)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS documentos_financeiros (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        tipo_documento VARCHAR(50) NOT NULL,
        origem_midia VARCHAR(100) NOT NULL,
        media_url TEXT NOT NULL,
        status_processamento VARCHAR(50) NOT NULL DEFAULT 'recebido',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    ALTER TABLE configuracoes_usuario
    ADD COLUMN IF NOT EXISTS documentos_onboarding_concluido BOOLEAN NOT NULL DEFAULT FALSE
    """,
    """
    ALTER TABLE configuracoes_usuario
    ADD COLUMN IF NOT EXISTS aguardando_documento BOOLEAN NOT NULL DEFAULT FALSE
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
