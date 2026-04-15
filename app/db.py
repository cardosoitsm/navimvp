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
        onboarding_state VARCHAR(50) NOT NULL DEFAULT 'account_snapshot_pending',
        pending_card_total INTEGER NOT NULL DEFAULT 0,
        pending_card_index INTEGER NOT NULL DEFAULT 0,
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
        extracted_json TEXT NULL,
        status_processamento VARCHAR(50) NOT NULL DEFAULT 'recebido',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS perfil_financeiro (
        user_id BIGINT PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
        saldo_atual_estimado NUMERIC(12, 2) NULL,
        renda_identificada NUMERIC(12, 2) NULL,
        despesas_fixas_estimadas NUMERIC(12, 2) NULL,
        pressao_cartao VARCHAR(20) NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS faturas_cartao (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        cartao_id BIGINT NULL,
        valor_total NUMERIC(12, 2) NOT NULL,
        vencimento DATE NULL,
        pagamento_minimo NUMERIC(12, 2) NULL,
        emissor VARCHAR(100) NULL,
        mes_referencia DATE NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cartoes_usuario (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        nome_cartao VARCHAR(100) NOT NULL,
        ordem INTEGER NOT NULL,
        dia_melhor_compra INTEGER NULL,
        limite_credito NUMERIC(12, 2) NULL,
        ativo BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (user_id, ordem)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS custos_mensais (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        descricao VARCHAR(255) NOT NULL,
        categoria VARCHAR(100) NULL,
        valor_medio NUMERIC(12, 2) NOT NULL,
        tipo_custo VARCHAR(20) NOT NULL CHECK (tipo_custo IN ('fixo', 'variavel')),
        confirmado BOOLEAN NOT NULL DEFAULT FALSE,
        origem VARCHAR(30) NOT NULL DEFAULT 'extrato',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    ALTER TABLE configuracoes_usuario
    ADD COLUMN IF NOT EXISTS onboarding_state VARCHAR(50) NOT NULL DEFAULT 'account_snapshot_pending'
    """,
    """
    ALTER TABLE configuracoes_usuario
    ADD COLUMN IF NOT EXISTS pending_card_total INTEGER NOT NULL DEFAULT 0
    """,
    """
    ALTER TABLE configuracoes_usuario
    ADD COLUMN IF NOT EXISTS pending_card_index INTEGER NOT NULL DEFAULT 0
    """,
    """
    ALTER TABLE configuracoes_usuario
    ADD COLUMN IF NOT EXISTS custos_onboarding_concluido BOOLEAN NOT NULL DEFAULT FALSE
    """,
    """
    ALTER TABLE configuracoes_usuario
    ADD COLUMN IF NOT EXISTS ultimo_topico VARCHAR(50) NULL
    """,
    """
    ALTER TABLE configuracoes_usuario
    ADD COLUMN IF NOT EXISTS ultimo_cartao_id BIGINT NULL
    """,
    """
    ALTER TABLE faturas_cartao
    ADD COLUMN IF NOT EXISTS cartao_id BIGINT NULL
    """,
    """
    ALTER TABLE cartoes_usuario
    ADD COLUMN IF NOT EXISTS dia_melhor_compra INTEGER NULL
    """,
    """
    ALTER TABLE cartoes_usuario
    ADD COLUMN IF NOT EXISTS limite_credito NUMERIC(12, 2) NULL
    """,
    """
    ALTER TABLE configuracoes_usuario
    ADD COLUMN IF NOT EXISTS documentos_onboarding_concluido BOOLEAN NOT NULL DEFAULT FALSE
    """,
    """
    ALTER TABLE configuracoes_usuario
    ADD COLUMN IF NOT EXISTS aguardando_documento BOOLEAN NOT NULL DEFAULT FALSE
    """,
    """
    ALTER TABLE documentos_financeiros
    ADD COLUMN IF NOT EXISTS extracted_json TEXT NULL
    """,
    """
    UPDATE configuracoes_usuario
    SET onboarding_state = CASE
        WHEN documentos_onboarding_concluido THEN 'onboarding_complete'
        WHEN orcamento_onboarding_concluido THEN 'card_count_pending'
        ELSE 'account_snapshot_pending'
    END
    WHERE onboarding_state IS NULL
       OR onboarding_state = ''
       OR onboarding_state = 'account_snapshot_pending'
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
