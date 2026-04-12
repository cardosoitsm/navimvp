from fastapi import HTTPException
from psycopg2 import IntegrityError

from app.auth import hash_password, verify_password
from app.db import get_cursor
from app.services.budgets import ensure_user_settings

AUTO_PASSWORD_PREFIX = "whatsapp-user:"


def _auto_password(numero_limpo: str) -> str:
    return hash_password(f"{AUTO_PASSWORD_PREFIX}{numero_limpo}")


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


def delete_user_account(email: str) -> bool:
    with get_cursor() as (conn, cursor):
        cursor.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
        result = cursor.fetchone()
        if not result:
            return False

        user_id = int(result[0])
        tables = (
            "orcamento_alertas",
            "orcamentos",
            "configuracoes_usuario",
            "confirmacoes_pendentes",
            "transacoes",
        )

        for table_name in tables:
            cursor.execute(f"DELETE FROM {table_name} WHERE user_id = %s", (user_id,))

        cursor.execute("DELETE FROM usuarios WHERE id = %s", (user_id,))
        conn.commit()
        return True
