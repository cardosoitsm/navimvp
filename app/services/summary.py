from app.db import get_cursor
from app.services.formatting import format_brl

# BUG-E2E-001 FIX: all spending queries now unify two data sources:
#   1. transacoes        — manual/confirmed individual transaction records
#   2. custos_mensais    — bank-import categorizations confirmed by the user (confirmado = true)
# This ensures that when a user confirms an imported bank-statement entry ("SIM"), it
# appears immediately in every spending summary, not just in the financial-profile views.

_COMBINED_SPENDING_CTE = """
    SELECT categoria, valor
    FROM transacoes
    WHERE user_id = %(user_id)s
    UNION ALL
    SELECT COALESCE(subcategoria, categoria) AS categoria, valor_medio AS valor
    FROM custos_mensais
    WHERE user_id = %(user_id)s AND confirmado = true
"""

_COMBINED_SPENDING_BY_CATEGORY_CTE = """
    SELECT categoria, valor
    FROM transacoes
    WHERE user_id = %(user_id)s AND categoria = %(categoria)s
    UNION ALL
    SELECT COALESCE(subcategoria, categoria) AS categoria, valor_medio AS valor
    FROM custos_mensais
    WHERE user_id = %(user_id)s AND confirmado = true
      AND COALESCE(subcategoria, categoria) = %(categoria)s
"""


def resumo_mes(user_id: int) -> str:
    with get_cursor() as (_, cursor):
        cursor.execute(
            f"""
            SELECT categoria, SUM(valor)
            FROM ({_COMBINED_SPENDING_CTE}) AS combined
            GROUP BY categoria
            ORDER BY categoria
            """,
            {"user_id": user_id},
        )
        dados = cursor.fetchall()

    if not dados:
        return "Você ainda não registrou gastos."

    total = 0.0
    linhas = ["Seus gastos:", ""]
    for categoria, valor in dados:
        valor_float = float(valor)
        linhas.append(f"- {categoria}: {format_brl(valor_float)}")
        total += valor_float

    linhas.append("")
    linhas.append(f"Total: {format_brl(total)}")
    return "\n".join(linhas)


def resumo_categoria(user_id: int, categoria: str) -> str:
    with get_cursor() as (_, cursor):
        cursor.execute(
            f"""
            SELECT COALESCE(SUM(valor), 0)
            FROM ({_COMBINED_SPENDING_BY_CATEGORY_CTE}) AS combined
            """,
            {"user_id": user_id, "categoria": categoria},
        )
        total = float(cursor.fetchone()[0])

    return f"Você gastou {format_brl(total)} com {categoria}."


def gerar_insight(user_id: int, categoria: str) -> str:
    with get_cursor() as (_, cursor):
        cursor.execute(
            f"""
            SELECT COALESCE(SUM(valor), 0)
            FROM ({_COMBINED_SPENDING_BY_CATEGORY_CTE}) AS combined
            """,
            {"user_id": user_id, "categoria": categoria},
        )
        total_categoria = float(cursor.fetchone()[0])

        cursor.execute(
            f"""
            SELECT COALESCE(SUM(valor), 0)
            FROM ({_COMBINED_SPENDING_CTE}) AS combined
            """,
            {"user_id": user_id},
        )
        total_geral = float(cursor.fetchone()[0])

    if total_geral <= 0:
        return ""

    percentual = (total_categoria / total_geral) * 100
    return (
        f"Você já gastou {format_brl(total_categoria)} em {categoria}.\n"
        f"Isso representa {percentual:.0f}% dos seus gastos."
    )


def listar_ultimas_transacoes(user_id: int, limite: int = 5) -> str:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT categoria, valor, tipo, created_at
            FROM (
                SELECT categoria, valor, tipo, created_at, id AS sort_id
                FROM transacoes
                WHERE user_id = %s
                UNION ALL
                SELECT
                    COALESCE(subcategoria, categoria) AS categoria,
                    valor_medio AS valor,
                    'despesa' AS tipo,
                    created_at,
                    id AS sort_id
                FROM custos_mensais
                WHERE user_id = %s AND confirmado = true
            ) AS combined
            ORDER BY created_at DESC, sort_id DESC
            LIMIT %s
            """,
            (user_id, user_id, limite),
        )
        dados = cursor.fetchall()

    if not dados:
        return "Você ainda não registrou transações."

    linhas = ["Seus últimos lançamentos:", ""]
    for categoria, valor, tipo, _ in dados:
        linhas.append(f"- {tipo} em {categoria}: {format_brl(float(valor))}")
    return "\n".join(linhas)
