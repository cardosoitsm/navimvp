import json
import unicodedata

from fastapi import HTTPException
from openai import OpenAI

from app.config import get_settings
from app.services.logger import get_logger
from app.db import get_cursor
from app.schemas import ParsedTransaction, PendingTransaction
from app.services.budgets import build_budget_feedback
from app.services.conversation import save_pending_confirmation, should_request_confirmation
from app.services.formatting import format_brl
from app.services.summary import gerar_insight


logger = get_logger("navi.chat")

REGRAS_CATEGORIAS = {
    "Alimentacao": ["ifood", "restaurante", "lanche", "pizza", "hamburguer"],
    "Transporte": ["uber", "99", "taxi", "gasolina", "combustivel"],
    "Moradia": ["aluguel", "condominio", "luz", "agua"],
    "Lazer": ["cinema", "netflix", "spotify", "bar"],
}


def _normalizar_texto_legado(valor: str) -> str:
    return (
        valor.lower()
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


def _normalizar_texto(valor: str) -> str:
    normalized = unicodedata.normalize("NFKD", valor.lower())
    return normalized.encode("ascii", "ignore").decode("ascii")


def classificar_categoria(texto: str) -> str | None:
    texto_normalizado = _normalizar_texto(texto)
    for categoria, palavras in REGRAS_CATEGORIAS.items():
        if any(palavra in texto_normalizado for palavra in palavras):
            return categoria
    return None


def _parse_openai_json(content: str) -> list[dict]:
    payload = content.strip()
    if "```" in payload:
        partes = payload.split("```")
        if len(partes) > 1:
            payload = partes[1].replace("json", "").strip()

    parsed = json.loads(payload)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    raise ValueError("Formato inesperado retornado pela IA")


def _extract_transactions_from_ai(text: str) -> list[ParsedTransaction]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY não configurada",
        )

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Responda apenas em JSON valido."},
            {
                "role": "user",
                "content": (
                    'Classifique a mensagem:\n\n'
                    f'"{text}"\n\n'
                    "Retorne uma lista de objetos no formato:\n"
                    '[{"tipo":"receita ou despesa","categoria":"categoria","valor":numero}]'
                ),
            },
        ],
    )

    content = response.choices[0].message.content or "[]"
    items = _parse_openai_json(content)
    transactions: list[ParsedTransaction] = []

    for item in items:
        transaction = ParsedTransaction.model_validate(item)
        if transaction.valor <= 0:
            continue
        transactions.append(transaction)

    return transactions


def _normalize_transaction(text: str, transaction: ParsedTransaction) -> PendingTransaction:
    categoria_regra = classificar_categoria(text)
    categoria = categoria_regra or _normalizar_texto(transaction.categoria or "outros")
    tipo = _normalizar_texto(transaction.tipo or "despesa")
    return PendingTransaction(tipo=tipo, categoria=categoria, valor=float(transaction.valor))


def _build_confirmation_message(transaction: PendingTransaction) -> str:
    return (
        "Acho que entendi assim:\n\n"
        f"- {transaction.tipo} em {transaction.categoria}: {format_brl(transaction.valor)}\n\n"
        'Se estiver certo, me responda "SIM". Se quiser corrigir, pode dizer "NÃO".'
    )


def process_user_message(text: str, user_id: int) -> dict[str, str]:
    try:
        transactions = _extract_transactions_from_ai(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("ai_parse_error text_len=%d", len(text))
        raise HTTPException(
            status_code=422,
            detail=(
                "Não consegui entender isso como uma transação. "
                "Pode tentar de outra forma? "
                'Por exemplo: "Gastei R$50 no Uber", "quanto gastei no mês?" '
                "ou me enviar um extrato ou fatura em imagem/PDF."
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ai_call_error text_len=%d", len(text))
        raise HTTPException(
            status_code=502,
            detail="Não foi possível interpretar a mensagem agora.",
        ) from exc

    if not transactions:
        raise HTTPException(
            status_code=422,
            detail=(
                "Não encontrei uma transação nessa mensagem. "
                'Se quiser, pode registrar um gasto (ex: "Gastei R$50 em Uber"), '
                "ver seus gastos do mês ou me enviar um extrato ou fatura em imagem/PDF."
            ),
        )

    normalized_transactions = [_normalize_transaction(text, transaction) for transaction in transactions]

    if len(normalized_transactions) == 1 and should_request_confirmation(text):
        pending = normalized_transactions[0]
        save_pending_confirmation(user_id, pending)
        return {"resposta": _build_confirmation_message(pending)}

    respostas: list[str] = []
    ultima_categoria = None

    with get_cursor() as (conn, cursor):
        for transaction in normalized_transactions:
            cursor.execute(
                """
                INSERT INTO transacoes (tipo, categoria, valor, user_id)
                VALUES (%s, %s, %s, %s)
                """,
                (transaction.tipo, transaction.categoria, transaction.valor, user_id),
            )

            respostas.append(f"- {transaction.categoria}: {format_brl(transaction.valor)}")
            ultima_categoria = transaction.categoria

        conn.commit()

    insight = gerar_insight(user_id, ultima_categoria) if ultima_categoria else ""
    budget_feedback = build_budget_feedback(user_id, ultima_categoria) if ultima_categoria else ""
    linhas = ["Anotei estas transações:", ""] + respostas
    if insight:
        linhas.extend(["", insight])
    if budget_feedback:
        linhas.extend(["", budget_feedback])

    return {"resposta": "\n".join(linhas)}
