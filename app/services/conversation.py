import json
import re

from fastapi import HTTPException

from app.db import get_cursor
from app.schemas import PendingTransaction

CONFIRMATION_YES = {"sim", "s", "confirmar", "confirmo", "ok", "pode confirmar"}
CONFIRMATION_NO = {"nao", "não", "n", "cancelar", "corrigir"}

QUERY_RECENT_PATTERNS = (
    "ultimos gastos",
    "últimos gastos",
    "ultimas transacoes",
    "ultimas transações",
    "ultimas despesas",
    "últimas despesas",
    "ultimos lancamentos",
    "ultimos lançamentos",
)


def normalize_text(text: str) -> str:
    return (
        text.lower()
        .strip()
        .replace("ã", "a")
        .replace("á", "a")
        .replace("â", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("õ", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )


def detect_intent(text: str) -> str:
    normalized = normalize_text(text)

    if normalized in CONFIRMATION_YES:
        return "confirm_yes"
    if normalized in CONFIRMATION_NO:
        return "confirm_no"
    if any(pattern in normalized for pattern in QUERY_RECENT_PATTERNS):
        return "recent_transactions"
    if "quanto gastei" in normalized:
        return "summary"
    return "transaction"


def should_request_confirmation(text: str) -> bool:
    normalized = normalize_text(text)
    has_amount = bool(re.search(r"r\$\s*\d", normalized) or re.search(r"\d+[,.]\d{2}", normalized))
    has_transaction_verb = any(
        verb in normalized
        for verb in ("gastei", "paguei", "comprei", "recebi", "ganhei", "transferi")
    )
    return not (has_amount and has_transaction_verb)


def save_pending_confirmation(user_id: int, transaction: PendingTransaction) -> None:
    payload_json = transaction.model_dump_json()
    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO confirmacoes_pendentes (user_id, payload_json)
            VALUES (%s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET payload_json = EXCLUDED.payload_json, created_at = NOW()
            """,
            (user_id, payload_json),
        )
        conn.commit()


def get_pending_confirmation(user_id: int) -> PendingTransaction | None:
    with get_cursor() as (_, cursor):
        cursor.execute(
            "SELECT payload_json FROM confirmacoes_pendentes WHERE user_id = %s",
            (user_id,),
        )
        result = cursor.fetchone()

    if not result:
        return None
    return PendingTransaction.model_validate(json.loads(result[0]))


def clear_pending_confirmation(user_id: int) -> None:
    with get_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM confirmacoes_pendentes WHERE user_id = %s", (user_id,))
        conn.commit()


def confirm_pending_transaction(user_id: int) -> dict[str, str]:
    pending = get_pending_confirmation(user_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Nao encontrei nenhuma transacao pendente para confirmar.")

    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO transacoes (tipo, categoria, valor, user_id)
            VALUES (%s, %s, %s, %s)
            """,
            (pending.tipo, pending.categoria, pending.valor, user_id),
        )
        cursor.execute("DELETE FROM confirmacoes_pendentes WHERE user_id = %s", (user_id,))
        conn.commit()

    return {
        "resposta": (
            "Transacao confirmada:\n\n"
            f"- {pending.categoria}: R${pending.valor:.2f}"
        )
    }


def reject_pending_transaction(user_id: int) -> str:
    pending = get_pending_confirmation(user_id)
    if not pending:
        return "Nao encontrei nenhuma transacao pendente para cancelar."

    clear_pending_confirmation(user_id)
    return "Tudo bem. Nao registrei a transacao. Me envie a correcao quando quiser."
