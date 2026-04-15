from app.db import get_cursor
from app.services.formatting import format_brl


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
            """
            SELECT COALESCE(SUM(valor), 0)
            FROM transacoes
            WHERE user_id = %s AND categoria = %s
            """,
            (user_id, categoria),
        )
        total = float(cursor.fetchone()[0])

    return f"Você gastou {format_brl(total)} com {categoria}."


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
        f"Você já gastou {format_brl(total_categoria)} em {categoria}.\n"
        f"Isso representa {percentual:.0f}% dos seus gastos."
    )


def listar_ultimas_transacoes(user_id: int, limite: int = 5) -> str:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT categoria, valor, tipo, created_at
            FROM transacoes
            WHERE user_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (user_id, limite),
        )
        dados = cursor.fetchall()

    if not dados:
        return "Você ainda não registrou transações."

    linhas = ["Seus últimos lançamentos:", ""]
    for categoria, valor, tipo, _ in dados:
        linhas.append(f"- {tipo} em {categoria}: {format_brl(float(valor))}")
    return "\n".join(linhas)
