import re
import unicodedata

from app.db import get_cursor

ACCOUNT_SNAPSHOT_PENDING = "account_snapshot_pending"
BUDGET_SETUP_PENDING = "budget_setup_pending"
DOCUMENT_ONBOARDING_PENDING = "document_onboarding_pending"
ONBOARDING_COMPLETE = "onboarding_complete"

SKIP_SNAPSHOT_WORDS = {"pular", "depois", "agora nao", "nao"}
BALANCE_KEYWORDS = ("saldo", "conta", "disponivel", "tenho", "hoje")


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower().strip())
    return normalized.encode("ascii", "ignore").decode("ascii")


def account_snapshot_prompt() -> str:
    return (
        "Para eu te orientar melhor desde o comeco, queria entender como esta sua conta hoje.\n\n"
        "Voce pode me dizer seu saldo atual ou me enviar o extrato de hoje.\n\n"
        'Se preferir, pode responder "PULAR" e seguimos mesmo assim.'
    )


def should_skip_account_snapshot(text: str) -> bool:
    return _normalize_text(text) in SKIP_SNAPSHOT_WORDS


def parse_balance_message(text: str) -> float | None:
    normalized = _normalize_text(text)
    amount_match = re.search(r"(?:r\$\s*)?(-?\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|-?\d+(?:,\d{2})?)", normalized)
    if not amount_match:
        return None

    if not any(keyword in normalized for keyword in BALANCE_KEYWORDS):
        compact = normalized.replace(" ", "")
        if compact != amount_match.group(0).replace(" ", ""):
            return None

    raw_amount = amount_match.group(1).replace(" ", "").replace(".", "").replace(",", ".")
    try:
        value = float(raw_amount)
    except ValueError:
        return None
    return value if value >= 0 else None


def get_onboarding_state(user_id: int) -> str:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT onboarding_state, orcamento_onboarding_concluido, documentos_onboarding_concluido
            FROM configuracoes_usuario
            WHERE user_id = %s
            """,
            (user_id,),
        )
        result = cursor.fetchone()

    if not result:
        return ACCOUNT_SNAPSHOT_PENDING

    onboarding_state, budget_done, document_done = result
    if onboarding_state:
        return str(onboarding_state)
    if document_done:
        return ONBOARDING_COMPLETE
    if budget_done:
        return DOCUMENT_ONBOARDING_PENDING
    return ACCOUNT_SNAPSHOT_PENDING


def set_onboarding_state(user_id: int, state: str) -> None:
    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            UPDATE configuracoes_usuario
            SET onboarding_state = %s,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (state, user_id),
        )
        conn.commit()


def save_current_balance(user_id: int, balance: float) -> None:
    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO perfil_financeiro (
                user_id,
                saldo_atual_estimado,
                updated_at
            )
            VALUES (%s, %s, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET
                saldo_atual_estimado = EXCLUDED.saldo_atual_estimado,
                updated_at = NOW()
            """,
            (user_id, balance),
        )
        conn.commit()

