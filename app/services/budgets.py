import re
import unicodedata
from decimal import Decimal, InvalidOperation

from app.db import get_cursor
from app.services.onboarding import DOCUMENT_ONBOARDING_PENDING, set_onboarding_state

SKIP_BUDGET_WORDS = {"pular", "depois", "agora nao", "agora nao.", "nao"}

CATEGORY_ALIASES = {
    "farmacia": "farmacia",
    "mercado": "mercado",
    "supermercado": "mercado",
    "alimentacao": "alimentacao",
    "alimentacao ": "alimentacao",
    "lazer": "lazer",
    "transporte": "transporte",
    "moradia": "moradia",
    "saude": "saude",
}

ALERT_LEVELS = (
    (100, "Voce ultrapassou o limite planejado para {categoria} neste mes."),
    (80, "Atencao: voce ja usou {percentual:.0f}% do seu limite de {categoria}."),
    (50, "Voce ja usou {percentual:.0f}% do seu limite de {categoria} neste mes."),
)


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower().strip())
    return normalized.encode("ascii", "ignore").decode("ascii")


def ensure_user_settings(user_id: int) -> None:
    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO configuracoes_usuario (user_id)
            VALUES (%s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id,),
        )
        conn.commit()


def is_budget_onboarding_completed(user_id: int) -> bool:
    ensure_user_settings(user_id)
    with get_cursor() as (_, cursor):
        cursor.execute(
            "SELECT orcamento_onboarding_concluido FROM configuracoes_usuario WHERE user_id = %s",
            (user_id,),
        )
        result = cursor.fetchone()
    return bool(result and result[0])


def mark_budget_onboarding_completed(user_id: int) -> None:
    ensure_user_settings(user_id)
    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            UPDATE configuracoes_usuario
            SET orcamento_onboarding_concluido = TRUE, updated_at = NOW()
            WHERE user_id = %s
            """,
            (user_id,),
        )
        conn.commit()
    set_onboarding_state(user_id, DOCUMENT_ONBOARDING_PENDING)


def onboarding_budget_prompt() -> str:
    return (
        "Antes de comecarmos de verdade, quero entender como voce gostaria de se organizar neste mes.\n\n"
        "Se quiser, me diga seus limites por categoria. Por exemplo:\n"
        "Farmacia 290, mercado 1200, lazer 1000\n\n"
        'Se preferir, pode responder "PULAR" e a gente configura isso depois.'
    )


def should_skip_budget_onboarding(text: str) -> bool:
    return _normalize_text(text) in SKIP_BUDGET_WORDS


def _normalize_category(raw_category: str) -> str:
    cleaned = _normalize_text(raw_category).strip(" :-")
    return CATEGORY_ALIASES.get(cleaned, cleaned)


def _parse_decimal(raw_amount: str) -> Decimal:
    cleaned = raw_amount.replace("r$", "").replace(".", "").replace(",", ".").strip()
    return Decimal(cleaned)


def parse_budget_message(text: str) -> dict[str, Decimal]:
    normalized = _normalize_text(text)
    budgets: dict[str, Decimal] = {}

    pattern = re.compile(
        r"(?P<categoria>farmacia|mercado|supermercado|alimentacao|lazer|transporte|moradia|saude)"
        r"\s*[:=-]?\s*(?:r\$)?\s*(?P<valor>\d+(?:[.,]\d{1,2})?)"
    )

    for match in pattern.finditer(normalized):
        categoria = _normalize_category(match.group("categoria"))
        try:
            valor = _parse_decimal(match.group("valor"))
        except InvalidOperation:
            continue

        if valor <= 0:
            continue
        budgets[categoria] = valor

    return budgets


def save_budgets(user_id: int, budgets: dict[str, Decimal]) -> None:
    ensure_user_settings(user_id)
    with get_cursor() as (conn, cursor):
        for categoria, valor in budgets.items():
            cursor.execute(
                """
                INSERT INTO orcamentos (user_id, categoria, limite_mensal)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, categoria)
                DO UPDATE SET limite_mensal = EXCLUDED.limite_mensal, updated_at = NOW()
                """,
                (user_id, categoria, valor),
            )
        cursor.execute(
            """
            UPDATE configuracoes_usuario
            SET orcamento_onboarding_concluido = TRUE, updated_at = NOW()
            WHERE user_id = %s
            """,
            (user_id,),
        )
        conn.commit()
    set_onboarding_state(user_id, DOCUMENT_ONBOARDING_PENDING)


def build_budget_setup_confirmation(budgets: dict[str, Decimal]) -> str:
    linhas = ["Perfeito. Ja deixei seus limites mensais anotados aqui comigo:", ""]
    for categoria, valor in budgets.items():
        linhas.append(f"- {categoria}: R${float(valor):.2f}")
    linhas.extend(
        [
            "",
            "Vou acompanhar isso com voce e te avisar quando algum limite estiver ficando apertado.",
            "Quando quiser, ja pode me mandar sua primeira transacao.",
        ]
    )
    return "\n".join(linhas)


def extract_budget_category(text: str) -> str | None:
    normalized = _normalize_text(text)
    for alias, categoria in CATEGORY_ALIASES.items():
        if alias in normalized:
            return categoria
    return None


def _current_budget_progress(user_id: int, categoria: str) -> tuple[float, float] | None:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT limite_mensal
            FROM orcamentos
            WHERE user_id = %s AND categoria = %s
            """,
            (user_id, categoria),
        )
        budget_row = cursor.fetchone()
        if not budget_row:
            return None

        cursor.execute(
            """
            SELECT COALESCE(SUM(valor), 0)
            FROM transacoes
            WHERE user_id = %s
              AND categoria = %s
              AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', NOW())
            """,
            (user_id, categoria),
        )
        spent_row = cursor.fetchone()

    return float(spent_row[0]), float(budget_row[0])


def build_budget_status_message(user_id: int, categoria: str) -> str:
    progress = _current_budget_progress(user_id, categoria)
    if not progress:
        return f"Voce ainda nao configurou um limite para {categoria}."

    gasto, limite = progress
    restante = limite - gasto
    percentual = (gasto / limite) * 100 if limite > 0 else 0

    return (
        f"Seu limite de {categoria} neste mes e R${limite:.2f}.\n"
        f"Voce ja gastou R${gasto:.2f} ({percentual:.0f}% do limite).\n"
        f"Ainda restam R${max(restante, 0):.2f}."
    )


def _register_alert_if_needed(user_id: int, categoria: str, percentual: float) -> str:
    nivel_disparado = None
    mensagem = ""
    for nivel, template in ALERT_LEVELS:
        if percentual >= nivel:
            nivel_disparado = nivel
            mensagem = template.format(categoria=categoria, percentual=percentual)
            break

    if nivel_disparado is None:
        return ""

    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            SELECT 1
            FROM orcamento_alertas
            WHERE user_id = %s
              AND categoria = %s
              AND mes_referencia = DATE_TRUNC('month', NOW())::date
              AND nivel_alerta = %s
            """,
            (user_id, categoria, nivel_disparado),
        )
        if cursor.fetchone():
            return ""

        cursor.execute(
            """
            INSERT INTO orcamento_alertas (user_id, categoria, mes_referencia, nivel_alerta)
            VALUES (%s, %s, DATE_TRUNC('month', NOW())::date, %s)
            """,
            (user_id, categoria, nivel_disparado),
        )
        conn.commit()

    return mensagem


def build_budget_feedback(user_id: int, categoria: str) -> str:
    progress = _current_budget_progress(user_id, categoria)
    if not progress:
        return ""

    gasto, limite = progress
    percentual = (gasto / limite) * 100 if limite > 0 else 0
    restante = max(limite - gasto, 0)
    linhas = [
        f"Seu limite de {categoria} neste mes e R${limite:.2f}.",
        f"Voce ja consumiu R${gasto:.2f} ({percentual:.0f}% do limite).",
        f"Ainda restam R${restante:.2f}.",
    ]

    alerta = _register_alert_if_needed(user_id, categoria, percentual)
    if alerta:
        linhas.extend(["", alerta])

    return "\n".join(linhas)
