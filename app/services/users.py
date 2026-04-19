from fastapi import HTTPException
from psycopg2 import IntegrityError

from app.auth import hash_password, verify_password
from app.db import get_cursor
from app.services.budgets import ensure_user_settings
from app.services.onboarding import USER_REGISTRATION_PENDING, set_onboarding_state

AUTO_PASSWORD_PREFIX = "whatsapp-user:"


def _auto_password(numero_limpo: str) -> str:
    return hash_password(f"{AUTO_PASSWORD_PREFIX}{numero_limpo}")


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = %s
        """,
        (table_name,),
    )
    return cursor.fetchone() is not None


def _tables_with_user_id(cursor) -> list[str]:
    cursor.execute(
        """
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND column_name = 'user_id'
        ORDER BY table_name
        """
    )
    return [str(row[0]) for row in cursor.fetchall()]


def get_or_create_whatsapp_user(numero: str) -> tuple[int, bool]:
    numero_limpo = numero.replace("whatsapp:", "").strip()

    with get_cursor() as (conn, cursor):
        cursor.execute("SELECT id FROM usuarios WHERE email = %s", (numero_limpo,))
        result = cursor.fetchone()

        if result:
            return int(result[0]), False

        cursor.execute(
            "INSERT INTO usuarios (email, senha) VALUES (%s, %s) RETURNING id",
            (numero_limpo, _auto_password(numero_limpo)),
        )
        created = cursor.fetchone()
        conn.commit()
        user_id = int(created[0])
        ensure_user_settings(user_id)
        set_onboarding_state(user_id, USER_REGISTRATION_PENDING)
        return user_id, True


def register_user(email: str, senha: str) -> int:
    with get_cursor() as (conn, cursor):
        try:
            cursor.execute(
                "INSERT INTO usuarios (email, senha) VALUES (%s, %s) RETURNING id",
                (email, hash_password(senha)),
            )
            created = cursor.fetchone()
            conn.commit()
            user_id = int(created[0])
            ensure_user_settings(user_id)
            return user_id
        except IntegrityError as exc:
            conn.rollback()
            raise HTTPException(status_code=409, detail="Usuario ja existe") from exc


def authenticate_user(email: str, senha: str) -> int:
    with get_cursor() as (_, cursor):
        cursor.execute(
            "SELECT id, senha FROM usuarios WHERE email = %s",
            (email,),
        )
        result = cursor.fetchone()

    if not result:
        raise HTTPException(status_code=401, detail="Usuario nao encontrado")

    user_id, hashed = result
    if not verify_password(senha, hashed):
        raise HTTPException(status_code=401, detail="Senha invalida")

    return int(user_id)


def get_user_locale(user_id: int) -> str:
    with get_cursor() as (_, cursor):
        cursor.execute("SELECT locale FROM usuarios WHERE id = %s", (user_id,))
        result = cursor.fetchone()
    return str(result[0]) if result and result[0] else "pt-BR"


def delete_user_account(email: str) -> bool:
    with get_cursor() as (conn, cursor):
        try:
            cursor.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
            result = cursor.fetchone()
            if not result:
                return False

            user_id = int(result[0])
            dynamic_tables = [
                table_name
                for table_name in _tables_with_user_id(cursor)
                if table_name != "usuarios" and _table_exists(cursor, table_name)
            ]

            for table_name in dynamic_tables:
                cursor.execute(f"DELETE FROM {table_name} WHERE user_id = %s", (user_id,))

            if _table_exists(cursor, "usuarios"):
                cursor.execute("DELETE FROM usuarios WHERE id = %s", (user_id,))

            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
