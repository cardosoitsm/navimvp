import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from openai import OpenAI

from app.config import get_settings
from app.db import get_cursor

ACCOUNT_SNAPSHOT_PENDING = "account_snapshot_pending"
BUDGET_SETUP_PENDING = "budget_setup_pending"
COST_REVIEW_PENDING = "cost_review_pending"
CARD_COUNT_PENDING = "card_count_pending"
CARD_NAMES_PENDING = "card_names_pending"
CARD_DETAILS_PENDING = "card_details_pending"
CARD_INVOICE_PENDING = "card_invoice_pending"
DOCUMENT_ONBOARDING_PENDING = "document_onboarding_pending"
ONBOARDING_COMPLETE = "onboarding_complete"

SKIP_SNAPSHOT_WORDS = {"pular", "depois", "agora nao", "nao"}
SKIP_CARD_SETUP_WORDS = {"pular", "depois", "agora nao", "nao", "nenhum", "0"}
CONFIRM_YES_WORDS = {"sim", "s", "pode ser", "faz sentido", "ok", "perfeito", "fechado"}
CONFIRM_NO_WORDS = {"nao", "não", "corrigir", "ajustar", "revisar"}
BALANCE_KEYWORDS = (
    "saldo",
    "saldo atual",
    "saldo da conta",
    "na conta",
    "em conta",
    "disponivel",
    "meu saldo",
)
NON_BALANCE_KEYWORDS = (
    "cartao",
    "cartoes",
    "fatura",
    "limite",
    "orcamento",
    "parcela",
    "emprestimo",
    "gastei",
    "gasto",
    "recebi",
    "transacao",
)
CARD_NUMBER_WORDS = {
    "zero": 0,
    "nenhum": 0,
    "um": 1,
    "uma": 1,
    "dois": 2,
    "duas": 2,
    "tres": 3,
    "três": 3,
    "quatro": 4,
    "cinco": 5,
}

FIXED_COST_KEYWORDS = (
    "aluguel",
    "condominio",
    "condomínio",
    "mensalidade",
    "seguro",
    "deb auto",
    "debito aut",
    "debito automatico",
    "débito aut",
    "telefone",
    "vivo",
    "claro",
    "tim",
    "internet",
    "energia",
    "agua",
    "água",
    "gas",
    "gás",
    "escola",
    "academia",
    "parcela",
    "financiamento",
)
VARIABLE_COST_KEYWORDS = (
    "mercado",
    "supermercado",
    "farmacia",
    "farmácia",
    "uber",
    "ifood",
    "restaurante",
    "lazer",
    "pix enviado",
    "boleto",
    "loterias",
)


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower().strip())
    return normalized.encode("ascii", "ignore").decode("ascii")


def account_snapshot_prompt() -> str:
    return (
        "Para eu te orientar melhor desde o comeco, queria entender como esta sua vida financeira hoje.\n\n"
        "Voce pode me dizer seu saldo atual ou me enviar o extrato de hoje.\n\n"
        'Se preferir, pode responder "PULAR" e seguimos mesmo assim.'
    )


def should_skip_account_snapshot(text: str) -> bool:
    return _normalize_text(text) in SKIP_SNAPSHOT_WORDS


def should_skip_card_setup(text: str) -> bool:
    normalized = _normalize_text(text)
    if normalized in SKIP_CARD_SETUP_WORDS:
        return True
    return "nao tenho cartao" in normalized or "nao quero cadastrar cartao" in normalized


def is_confirmation_yes(text: str) -> bool:
    normalized = _normalize_text(text)
    return normalized in CONFIRM_YES_WORDS


def is_confirmation_no(text: str) -> bool:
    normalized = _normalize_text(text)
    return normalized in CONFIRM_NO_WORDS


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


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    )
    return cursor.fetchone() is not None


def parse_balance_message(text: str) -> float | None:
    normalized = _normalize_text(text)
    if any(keyword in normalized for keyword in NON_BALANCE_KEYWORDS):
        return None

    amount_matches = re.findall(r"(?:r\$\s*)?(-?\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|-?\d+(?:,\d{2})?)", normalized)
    if len(amount_matches) != 1:
        return None

    amount_text = amount_matches[0]
    has_balance_context = any(keyword in normalized for keyword in BALANCE_KEYWORDS)
    compact_message = re.sub(r"\s+", "", normalized)
    compact_amount = re.sub(r"\s+", "", amount_text)

    if not has_balance_context:
        if compact_message not in {
            compact_amount,
            f"r${compact_amount}",
            f"saldo{compact_amount}",
            f"saldor${compact_amount}",
        }:
            return None

    raw_amount = amount_text.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        value = float(raw_amount)
    except ValueError:
        return None
    return value if value >= 0 else None


def card_count_prompt() -> str:
    return (
        "Agora me conta uma coisa importante: quantos cartoes voce quer acompanhar comigo?\n\n"
        "Pode me responder algo como 1, 2 ou 3.\n\n"
        'Se preferir deixar isso para depois, pode responder "PULAR".'
    )


def cost_review_prompt(fixed_costs: list[dict[str, float | str]], variable_costs: list[dict[str, float | str]]) -> str:
    lines = [
        "Pelo que apareceu no seu extrato, eu ja consegui montar uma primeira leitura dos seus custos mensais.",
    ]

    if fixed_costs:
        lines.extend(["", "Custos que parecem mais fixos:"])
        for item in fixed_costs[:4]:
            lines.append(f"- {item['descricao']}: R${float(item['valor']):.2f}")

    if variable_costs:
        lines.extend(["", "Custos que parecem mais variaveis:"])
        for item in variable_costs[:4]:
            lines.append(f"- {item['descricao']}: R${float(item['valor']):.2f}")

    lines.extend(
        [
            "",
            'Se fizer sentido, me responda "SIM" e eu considero essa base daqui para frente.',
            'Se preferir revisar depois, pode responder "PULAR" e seguimos.',
        ]
    )
    return "\n".join(lines)


def card_names_prompt(total: int) -> str:
    if total <= 1:
        return (
            "Perfeito. Como voce quer chamar esse cartao por aqui?\n\n"
            "Pode ser o nome do banco ou um apelido que faca sentido para voce."
        )

    return (
        f"Entao vamos cadastrar esses {total} cartoes.\n\n"
        "Me diga como voce quer chamar cada um deles, de preferencia na ordem, separado por virgula.\n"
        "Por exemplo: Nubank, Itau, Cartao da Casa"
    )


def card_details_prompt(card_name: str) -> str:
    return (
        f"Agora me ajuda com mais um detalhe do {card_name}.\n\n"
        "Qual e o melhor dia de compra e qual e o limite desse cartao?\n"
        "Por exemplo: melhor dia 20 e limite 5000\n\n"
        'Se preferir, pode responder "PULAR".'
    )


def card_invoice_prompt(card_name: str) -> str:
    return (
        f"Perfeito. Agora pode me mandar a fatura atual do {card_name}.\n\n"
        'Pode ser imagem ou PDF. Se preferir pular esta fatura por enquanto, responda "PULAR".'
    )


def parse_card_count(text: str) -> int | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None

    if "nao tenho cartao" in normalized or normalized in {"nenhum", "0"}:
        return 0

    digit_match = re.search(r"\b(\d{1,2})\b", normalized)
    if digit_match:
        return int(digit_match.group(1))

    for word, value in CARD_NUMBER_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", normalized):
            return value
    return None


def get_pending_card_total(user_id: int) -> int:
    with get_cursor() as (_, cursor):
        if _column_exists(cursor, "configuracoes_usuario", "pending_card_total"):
            cursor.execute(
                """
                SELECT pending_card_total
                FROM configuracoes_usuario
                WHERE user_id = %s
                """,
                (user_id,),
            )
            result = cursor.fetchone()
            if result and result[0] is not None and int(result[0]) > 0:
                return int(result[0])

        if _table_exists(cursor, "cartoes_usuario"):
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM cartoes_usuario
                WHERE user_id = %s AND ativo = FALSE
                """,
                (user_id,),
            )
            result = cursor.fetchone()
            return int(result[0]) if result and result[0] is not None else 0

    return 0


def save_card_count(user_id: int, total: int) -> None:
    total = max(total, 0)
    with get_cursor() as (conn, cursor):
        if _column_exists(cursor, "configuracoes_usuario", "pending_card_total") and _column_exists(
            cursor, "configuracoes_usuario", "pending_card_index"
        ):
            cursor.execute(
                """
                UPDATE configuracoes_usuario
                SET pending_card_total = %s,
                    pending_card_index = 0,
                    updated_at = NOW()
                WHERE user_id = %s
                """,
                (total, user_id),
            )
        else:
            cursor.execute(
                """
                UPDATE configuracoes_usuario
                SET updated_at = NOW()
                WHERE user_id = %s
                """,
                (user_id,),
            )

        if _table_exists(cursor, "cartoes_usuario"):
            cursor.execute("DELETE FROM cartoes_usuario WHERE user_id = %s", (user_id,))
            for ordem in range(1, total + 1):
                cursor.execute(
                    """
                    INSERT INTO cartoes_usuario (user_id, nome_cartao, ordem, ativo)
                    VALUES (%s, %s, %s, FALSE)
                    """,
                    (user_id, f"Pendente {ordem}", ordem),
                )
        conn.commit()


def parse_card_names(text: str, expected_count: int) -> list[str]:
    if expected_count <= 0:
        return []

    cleaned_text = text.strip()
    if not cleaned_text:
        return []

    if expected_count == 1:
        candidate = re.sub(
            r"^(meu\s+cartao|meu\s+cartão|cartao|cartão|nome|o nome e|o nome é)\s+",
            "",
            cleaned_text,
            flags=re.IGNORECASE,
        ).strip(" .:-")
        return [candidate] if candidate else []

    parts = re.split(r"\s*(?:,|;|\n|\be\b)\s*", cleaned_text, flags=re.IGNORECASE)
    normalized_parts: list[str] = []
    for index, part in enumerate(parts):
        candidate = part.strip(" .:-")
        if not candidate:
            continue
        if index == 0:
            candidate = re.sub(
                r"^(os nomes sao|os nomes são|nomes|meus cartoes|meus cartões|cartoes|cartões)\s+",
                "",
                candidate,
                flags=re.IGNORECASE,
            ).strip(" .:-")
        if candidate:
            normalized_parts.append(candidate)

    if len(normalized_parts) != expected_count:
        return []
    return normalized_parts


def save_card_names(user_id: int, names: list[str]) -> None:
    with get_cursor() as (conn, cursor):
        if _table_exists(cursor, "cartoes_usuario"):
            cursor.execute("DELETE FROM cartoes_usuario WHERE user_id = %s", (user_id,))
            for ordem, nome in enumerate(names, start=1):
                cursor.execute(
                    """
                    INSERT INTO cartoes_usuario (user_id, nome_cartao, ordem)
                    VALUES (%s, %s, %s)
                    """,
                    (user_id, nome.strip(), ordem),
                )
        if _column_exists(cursor, "configuracoes_usuario", "pending_card_total") and _column_exists(
            cursor, "configuracoes_usuario", "pending_card_index"
        ):
            cursor.execute(
                """
                UPDATE configuracoes_usuario
                SET pending_card_total = %s,
                    pending_card_index = 0,
                    updated_at = NOW()
                WHERE user_id = %s
                """,
                (len(names), user_id),
            )
        else:
            cursor.execute(
                """
                UPDATE configuracoes_usuario
                SET updated_at = NOW()
                WHERE user_id = %s
                """,
                (user_id,),
            )
        conn.commit()


def get_card_names(user_id: int) -> list[str]:
    with get_cursor() as (_, cursor):
        if not _table_exists(cursor, "cartoes_usuario"):
            return []
        if _column_exists(cursor, "cartoes_usuario", "ativo"):
            cursor.execute(
                """
                SELECT nome_cartao
                FROM cartoes_usuario
                WHERE user_id = %s AND ativo = TRUE
                ORDER BY ordem ASC
                """,
                (user_id,),
            )
        else:
            cursor.execute(
                """
                SELECT nome_cartao
                FROM cartoes_usuario
                WHERE user_id = %s
                ORDER BY ordem ASC
                """,
                (user_id,),
            )
        rows = cursor.fetchall()
    return [str(row[0]) for row in rows]


def get_cards(user_id: int) -> list[dict[str, int | str | None]]:
    with get_cursor() as (_, cursor):
        if not _table_exists(cursor, "cartoes_usuario"):
            return []
        cursor.execute(
            """
            SELECT id, nome_cartao, ordem, dia_melhor_compra, limite_credito
            FROM cartoes_usuario
            WHERE user_id = %s
            ORDER BY ordem ASC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
    return [
        {
            "id": int(row[0]),
            "nome_cartao": str(row[1]),
            "ordem": int(row[2]),
            "dia_melhor_compra": int(row[3]) if row[3] is not None else None,
            "limite_credito": float(row[4]) if row[4] is not None else None,
        }
        for row in rows
    ]


def get_pending_card_index(user_id: int) -> int:
    with get_cursor() as (_, cursor):
        if not _column_exists(cursor, "configuracoes_usuario", "pending_card_index"):
            return 0
        cursor.execute(
            """
            SELECT pending_card_index
            FROM configuracoes_usuario
            WHERE user_id = %s
            """,
            (user_id,),
        )
        result = cursor.fetchone()
    return int(result[0]) if result and result[0] is not None else 0


def set_pending_card_index(user_id: int, index: int) -> None:
    with get_cursor() as (conn, cursor):
        if _column_exists(cursor, "configuracoes_usuario", "pending_card_index"):
            cursor.execute(
                """
                UPDATE configuracoes_usuario
                SET pending_card_index = %s,
                    updated_at = NOW()
                WHERE user_id = %s
                """,
                (max(index, 0), user_id),
            )
        else:
            cursor.execute(
                """
                UPDATE configuracoes_usuario
                SET updated_at = NOW()
                WHERE user_id = %s
                """,
                (user_id,),
            )
        conn.commit()


def get_current_card(user_id: int) -> dict[str, int | str | None] | None:
    cards = get_cards(user_id)
    if not cards:
        return None
    index = get_pending_card_index(user_id)
    if index < 0 or index >= len(cards):
        return None
    return cards[index]


def advance_card_progress(user_id: int) -> dict[str, int | str | None] | None:
    cards = get_cards(user_id)
    if not cards:
        return None
    next_index = get_pending_card_index(user_id) + 1
    set_pending_card_index(user_id, next_index)
    if next_index >= len(cards):
        return None
    return cards[next_index]


def _parse_decimal_value(raw_amount: str) -> Decimal:
    cleaned = raw_amount.lower().replace("r$", "").replace(" ", "").strip().rstrip(".")
    multiplier = Decimal("1")

    if cleaned.endswith("k"):
        multiplier = Decimal("1000")
        cleaned = cleaned[:-1]
    elif cleaned.endswith("m"):
        multiplier = Decimal("1000000")
        cleaned = cleaned[:-1]

    cleaned = cleaned.replace(".", "").replace(",", ".")
    return Decimal(cleaned) * multiplier


def parse_card_details_message(text: str) -> tuple[int | None, float | None]:
    normalized = _normalize_text(text)
    day_match = re.search(r"\b([1-9]|[12][0-9]|3[01])\b", normalized)
    day = int(day_match.group(1)) if day_match else None

    amount_matches = re.findall(
        r"(?:r\$\s*)?(\d{1,3}(?:\.\d{3})*(?:,\d{2})?[km]?\.?|\d+(?:,\d{2})?[km]?\.?)",
        normalized,
    )
    limit_value = None
    if amount_matches:
        try:
            parsed_values = [_parse_decimal_value(match) for match in amount_matches]
            limit_value = float(max(parsed_values))
        except (InvalidOperation, ValueError):
            limit_value = None

    return day, limit_value


def save_current_card_details(user_id: int, day: int | None, limit_value: float | None) -> dict[str, int | str | None] | None:
    current_card = get_current_card(user_id)
    if not current_card:
        return None

    with get_cursor() as (conn, cursor):
        if _column_exists(cursor, "cartoes_usuario", "dia_melhor_compra") and _column_exists(
            cursor, "cartoes_usuario", "limite_credito"
        ):
            cursor.execute(
                """
                UPDATE cartoes_usuario
                SET dia_melhor_compra = %s,
                    limite_credito = %s
                WHERE id = %s
                """,
                (day, limit_value, current_card["id"]),
            )
        conn.commit()

    updated_card = dict(current_card)
    updated_card["dia_melhor_compra"] = day
    updated_card["limite_credito"] = limit_value
    return updated_card


def _latest_statement_analysis(user_id: int) -> dict | None:
    with get_cursor() as (_, cursor):
        if not _table_exists(cursor, "documentos_financeiros"):
            return None
        cursor.execute(
            """
            SELECT extracted_json
            FROM documentos_financeiros
            WHERE user_id = %s
              AND tipo_documento = 'extrato'
              AND extracted_json IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()

    if not row or not row[0]:
        return None

    try:
        parsed = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_amount(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return abs(float(value))
    except (TypeError, ValueError):
        return None


def _classify_cost_type(description: str) -> str | None:
    normalized = _normalize_text(description)
    if any(keyword in normalized for keyword in FIXED_COST_KEYWORDS):
        return "fixo"
    if any(keyword in normalized for keyword in VARIABLE_COST_KEYWORDS):
        return "variavel"
    return None


def infer_cost_candidates(user_id: int) -> tuple[list[dict[str, float | str]], list[dict[str, float | str]]]:
    analysis = _latest_statement_analysis(user_id)
    if not analysis:
        return [], []

    debit_entries = analysis.get("debit_entries")
    if not isinstance(debit_entries, list):
        return [], []

    fixed_costs: list[dict[str, float | str]] = []
    variable_costs: list[dict[str, float | str]] = []
    seen_keys: set[str] = set()

    for entry in debit_entries:
        if not isinstance(entry, dict):
            continue
        description = str(entry.get("description") or "").strip()
        amount = _normalize_amount(entry.get("amount"))
        if not description or amount is None:
            continue

        cost_type = _classify_cost_type(description)
        if not cost_type:
            continue

        key = _normalize_text(description)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        payload = {"descricao": description, "valor": amount}
        if cost_type == "fixo":
            fixed_costs.append(payload)
        else:
            variable_costs.append(payload)

    return fixed_costs[:4], variable_costs[:4]


def has_cost_review_candidates(user_id: int) -> bool:
    fixed_costs, variable_costs = infer_cost_candidates(user_id)
    return bool(fixed_costs or variable_costs)


def is_cost_review_completed(user_id: int) -> bool:
    with get_cursor() as (_, cursor):
        if not _column_exists(cursor, "configuracoes_usuario", "custos_onboarding_concluido"):
            return False
        cursor.execute(
            "SELECT custos_onboarding_concluido FROM configuracoes_usuario WHERE user_id = %s",
            (user_id,),
        )
        row = cursor.fetchone()
    return bool(row and row[0])


def save_cost_candidates(user_id: int, confirmed: bool) -> None:
    fixed_costs, variable_costs = infer_cost_candidates(user_id)
    with get_cursor() as (conn, cursor):
        if _table_exists(cursor, "custos_mensais"):
            cursor.execute("DELETE FROM custos_mensais WHERE user_id = %s AND origem = 'extrato'", (user_id,))
            if confirmed:
                for item in fixed_costs:
                    cursor.execute(
                        """
                        INSERT INTO custos_mensais (user_id, descricao, valor_medio, tipo_custo, confirmado, origem)
                        VALUES (%s, %s, %s, 'fixo', TRUE, 'extrato')
                        """,
                        (user_id, item["descricao"], item["valor"]),
                    )
                for item in variable_costs:
                    cursor.execute(
                        """
                        INSERT INTO custos_mensais (user_id, descricao, valor_medio, tipo_custo, confirmado, origem)
                        VALUES (%s, %s, %s, 'variavel', TRUE, 'extrato')
                        """,
                        (user_id, item["descricao"], item["valor"]),
                    )

        if _column_exists(cursor, "configuracoes_usuario", "custos_onboarding_concluido"):
            cursor.execute(
                """
                UPDATE configuracoes_usuario
                SET custos_onboarding_concluido = TRUE,
                    updated_at = NOW()
                WHERE user_id = %s
                """,
                (user_id,),
            )
        conn.commit()


def parse_card_names_flexible(text: str, expected_count: int) -> list[str]:
    if expected_count <= 0:
        return []

    cleaned_text = text.strip()
    if not cleaned_text:
        return []

    if ":" in cleaned_text:
        left_side, right_side = cleaned_text.split(":", 1)
        normalized_left = _normalize_text(left_side)
        if "cart" in normalized_left or "nome" in normalized_left:
            cleaned_text = right_side.strip(" .:-")
    else:
        cleaned_text = re.sub(
            r"^(?:meus?\s+cart[oõ]es?\s+s[aã]o|os\s+cart[oõ]es?\s+s[aã]o|cart[oõ]es?\s+s[aã]o|os\s+nomes?\s+s[aã]o|nomes?\s+s[aã]o|meus?\s+cart[oõ]es?|cart[oõ]es?|o\s+nome\s+[ée]|nome)\s*",
            "",
            cleaned_text,
            flags=re.IGNORECASE,
        ).strip(" .:-")

    if expected_count == 1:
        candidate = cleaned_text.strip(" .:-")
        return [candidate] if candidate else []

    parts = re.split(r"\s*(?:,|;|\n|\be\b)\s*", cleaned_text, flags=re.IGNORECASE)
    names = [part.strip(" .:-") for part in parts if part.strip(" .:-")]
    if len(names) != expected_count:
        return []
    return names


def parse_card_names_assisted(text: str, expected_count: int) -> list[str]:
    def _looks_like_card_name(candidate: str) -> bool:
        normalized_candidate = _normalize_text(candidate)
        forbidden_markers = (
            "quero chamar",
            "vou chamar",
            "cartoes",
            "cartao",
            "nome",
            "claro",
            "perfeito",
            "esses cartoes",
            "estes cartoes",
        )
        return not any(marker in normalized_candidate for marker in forbidden_markers)

    cleaned_text = text.strip()
    if not cleaned_text or expected_count <= 0:
        return []

    if ":" in cleaned_text:
        left_side, right_side = cleaned_text.split(":", 1)
        normalized_left = _normalize_text(left_side)
        if any(keyword in normalized_left for keyword in ("cart", "nome", "chamar")):
            cleaned_text = right_side.strip(" .:-")
    else:
        cleaned_text = re.sub(
            r"^(?:claro|perfeito|beleza|ok|tudo bem)\W*",
            "",
            cleaned_text,
            flags=re.IGNORECASE,
        ).strip()
        cleaned_text = re.sub(
            r"^(?:quero\s+chamar\s+(?:estes?|esses|meus)?\s*cart[oõ]es?\s+de|"
            r"vou\s+chamar\s+(?:estes?|esses|meus)?\s*cart[oõ]es?\s+de|"
            r"os\s+cart[oõ]es?\s+s[aã]o|"
            r"meus?\s+cart[oõ]es?\s+s[aã]o|"
            r"cart[oõ]es?\s+s[aã]o|"
            r"os\s+nomes?\s+s[aã]o|"
            r"nomes?\s+s[aã]o)\s*",
            "",
            cleaned_text,
            flags=re.IGNORECASE,
        ).strip(" .:-")

    names = parse_card_names_flexible(cleaned_text, expected_count)
    if names and all(_looks_like_card_name(name) for name in names):
        return names

    settings = get_settings()
    if not settings.openai_api_key:
        return []

    try:
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extraia apenas os nomes dos cartoes citados pelo usuario e retorne somente JSON valido no formato "
                        '{"card_names":["nome 1","nome 2"]}. '
                        "Ignore saudacoes, confirmacoes e o resto da frase."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Frase do usuario: {text}\n"
                        f"Trecho ja limpo: {cleaned_text}\n"
                        f"Quantidade esperada de cartoes: {expected_count}\n"
                        "Responda somente com a lista dos nomes, sem repetir o resto da frase."
                    ),
                },
            ],
        )
        payload = (response.choices[0].message.content or "{}").strip()
        if "```" in payload:
            parts = payload.split("```")
            if len(parts) > 1:
                payload = parts[1].replace("json", "").strip()

        data = json.loads(payload)
        if not isinstance(data, dict):
            return []

        raw_names = data.get("card_names")
        if not isinstance(raw_names, list):
            return []

        normalized_names = [str(name).strip(" .:-") for name in raw_names if str(name).strip(" .:-")]
        if len(normalized_names) != expected_count:
            return []
        if not all(_looks_like_card_name(name) for name in normalized_names):
            return []

        return normalized_names
    except Exception:
        return []


def build_card_setup_confirmation(names: list[str]) -> str:
    if not names:
        return "Tudo bem. A gente pode cadastrar seus cartoes depois."

    if len(names) == 1:
        return f"Perfeito. Vou acompanhar esse cartao por aqui como {names[0]}."

    listed_names = ", ".join(names[:-1]) + f" e {names[-1]}"
    return f"Perfeito. Vou acompanhar esses cartoes por aqui como {listed_names}."


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
        return CARD_COUNT_PENDING
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
