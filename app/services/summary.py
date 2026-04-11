from app.db import get_cursor


def resumo_mes(user_id: int) -> str:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT categoria, SUM(valor)
            FROM transacoes
            WHERE user_id = %s
            GROUP BY categoria
            ORDER BY categoria
            """,
            (user_id,),
        )
        dados = cursor.fetchall()

    if not dados:
        return "Voce ainda nao registrou gastos."

    total = 0.0
    linhas = ["Seus gastos:", ""]
    for categoria, valor in dados:
        valor_float = float(valor)
        linhas.append(f"- {categoria}: R${valor_float:.2f}")
        total += valor_float

    linhas.append("")
    linhas.append(f"Total: R${total:.2f}")
    return "\n".join(linhas)


def resumo_categoria(user_id: int, categoria: str) -> str:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT COALESCE(SUM(valor), 0)
            FROM transacoes
            WHERE user_id = %s AND categoria = %s
            """,
            (user_id, categoria),
        )
        total = float(cursor.fetchone()[0])

    return f"Voce gastou R${total:.2f} com {categoria}"


def gerar_insight(user_id: int, categoria: str) -> str:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT COALESCE(SUM(valor), 0)
            FROM transacoes
            WHERE user_id = %s AND categoria = %s
            """,
            (user_id, categoria),
        )
        total_categoria = float(cursor.fetchone()[0])

        cursor.execute(
            """
            SELECT COALESCE(SUM(valor), 0)
            FROM transacoes
            WHERE user_id = %s
            """,
            (user_id,),
        )
        total_geral = float(cursor.fetchone()[0])

    if total_geral <= 0:
        return ""

    percentual = (total_categoria / total_geral) * 100
    return (
        f"Voce ja gastou R${total_categoria:.2f} em {categoria}\n"
        f"Isso representa {percentual:.0f}% dos seus gastos."
    )
