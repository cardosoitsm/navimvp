import json
import re
import unicodedata

from fastapi import HTTPException

from app.db import get_cursor
from app.services.budgets import build_budget_feedback
from app.schemas import PendingTransaction

CONFIRMATION_YES = {"sim", "s", "confirmar", "confirmo", "ok", "pode confirmar"}
CONFIRMATION_NO = {"nao", "n", "cancelar", "corrigir"}

QUERY_RECENT_PATTERNS = (
    "ultimos gastos",
    "ultimas transacoes",
    "ultimas despesas",
    "ultimos lancamentos",
)

QUERY_BUDGET_PATTERNS = (
    "quanto ainda posso gastar",
    "como esta meu limite",
    "como esta meu orcamento",
    "orcamento",
    "limite",
)

QUERY_INVOICE_PATTERNS = (
    "qual o valor da minha fatura",
    "qual o valor da fatura",
    "quanto esta a fatura",
    "quanto está a fatura",
    "valor da fatura",
    "minha fatura do",
    "fatura do santander",
    "fatura do bradesco",
    "fatura do nubank",
)

QUERY_FINANCIAL_HEALTH_PATTERNS = (
    "como esta minha saude financeira",
    "como esta minha vida financeira",
    "como esta minha situacao financeira",
    "como está minha saúde financeira",
    "como está minha vida financeira",
    "como está minha situação financeira",
    "minha saude financeira",
    "minha saúde financeira",
    "minha vida financeira",
    "minha situacao financeira",
    "minha situação financeira",
    "quao comprometida",
    "quão comprometida",
)

DOCUMENT_PATTERNS = (
    "extrato",
    "fatura",
    "pdf",
    "documento",
    "arquivo",
    "boleto do cartao",
)

CARD_SETUP_PATTERNS = (
    "cadastrar cartao",
    "cadastrar cartoes",
    "cadastrar meus cartoes",
    "acompanhar cartao",
    "acompanhar cartoes",
    "meus cartoes",
    "meus cartões",
    "quero cadastrar meu cartao",
    "quero cadastrar meus cartoes",
)


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower().strip())
    return normalized.encode("ascii", "ignore").decode("ascii")


def detect_intent(text: str) -> str:
    normalized = normalize_text(text)

    if normalized in CONFIRMATION_YES:
        return "confirm_yes"
    if normalized in CONFIRMATION_NO:
        return "confirm_no"
    if any(pattern in normalized for pattern in QUERY_RECENT_PATTERNS):
        return "recent_transactions"
    if any(pattern in normalized for pattern in QUERY_BUDGET_PATTERNS):
        return "budget_status"
    if any(pattern in normalized for pattern in QUERY_INVOICE_PATTERNS):
        return "invoice_status"
    if any(pattern in normalized for pattern in QUERY_FINANCIAL_HEALTH_PATTERNS):
        return "financial_health"
    if any(pattern in normalized for pattern in CARD_SETUP_PATTERNS):
        return "card_setup_request"
    if any(pattern in normalized for pattern in DOCUMENT_PATTERNS):
        return "document_request"
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

    resposta = (
        "Transacao confirmada:\n\n"
        f"- {pending.categoria}: R${pending.valor:.2f}"
    )
    budget_feedback = build_budget_feedback(user_id, pending.categoria)
    if budget_feedback:
        resposta = f"{resposta}\n\n{budget_feedback}"

    return {"resposta": resposta}


def reject_pending_transaction(user_id: int) -> str:
    pending = get_pending_confirmation(user_id)
    if not pending:
        return "Nao encontrei nenhuma transacao pendente para cancelar."

    clear_pending_confirmation(user_id)
    return "Tudo bem. Nao registrei a transacao. Me envie a correcao quando quiser."
