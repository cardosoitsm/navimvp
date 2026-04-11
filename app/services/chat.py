import json

from fastapi import HTTPException
from openai import OpenAI

from app.config import get_settings
from app.db import get_cursor
from app.schemas import ParsedTransaction
from app.services.summary import gerar_insight


REGRAS_CATEGORIAS = {
    "alimentacao": ["ifood", "restaurante", "lanche", "pizza", "hamburguer"],
    "transporte": ["uber", "99", "taxi", "gasolina", "combustivel"],
    "moradia": ["aluguel", "condominio", "luz", "agua"],
    "lazer": ["cinema", "netflix", "spotify", "bar"],
}


def _normalizar_texto(valor: str) -> str:
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
            detail="OPENAI_API_KEY nao configurada",
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


def process_user_message(text: str, user_id: int) -> dict[str, str]:
    try:
        transactions = _extract_transactions_from_ai(text)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail="Nao consegui entender a transacao. Tente algo como: Gastei R$50 em Uber.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Nao foi possivel interpretar a mensagem agora.",
        ) from exc

    if not transactions:
        raise HTTPException(
            status_code=422,
            detail="Nao encontrei uma transacao valida na sua mensagem.",
        )

    categoria_regra = classificar_categoria(text)

    respostas: list[str] = []
    ultima_categoria = None

    with get_cursor() as (conn, cursor):
        for transaction in transactions:
            categoria = categoria_regra or _normalizar_texto(transaction.categoria or "outros")
            tipo = _normalizar_texto(transaction.tipo or "despesa")
            valor = float(transaction.valor)

            cursor.execute(
                """
                INSERT INTO transacoes (tipo, categoria, valor, user_id)
                VALUES (%s, %s, %s, %s)
                """,
                (tipo, categoria, valor, user_id),
            )

            respostas.append(f"- {categoria}: R${valor:.2f}")
            ultima_categoria = categoria

        conn.commit()

    insight = gerar_insight(user_id, ultima_categoria) if ultima_categoria else ""
    linhas = ["Transacoes registradas:", ""] + respostas
    if insight:
        linhas.extend(["", insight])

    return {"resposta": "\n".join(linhas)}
