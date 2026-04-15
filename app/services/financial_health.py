from app.db import get_cursor
from app.services.formatting import format_brl


def build_financial_health_message(user_id: int) -> str:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT saldo_atual_estimado, renda_identificada, despesas_fixas_estimadas, pressao_cartao
            FROM perfil_financeiro
            WHERE user_id = %s
            """,
            (user_id,),
        )
        profile = cursor.fetchone()

        cursor.execute(
            """
            SELECT COALESCE(SUM(valor_medio), 0)
            FROM custos_mensais
            WHERE user_id = %s
              AND tipo_custo = 'fixo'
              AND confirmado = TRUE
            """,
            (user_id,),
        )
        confirmed_fixed_costs = float(cursor.fetchone()[0])

        cursor.execute(
            """
            SELECT COALESCE(SUM(valor_total), 0)
            FROM (
                SELECT DISTINCT ON (COALESCE(cartao_id, id))
                    valor_total
                FROM faturas_cartao
                WHERE user_id = %s
                ORDER BY COALESCE(cartao_id, id), created_at DESC
            ) latest_invoices
            """,
            (user_id,),
        )
        invoice_total = float(cursor.fetchone()[0])

        cursor.execute(
            """
            SELECT COALESCE(SUM(valor), 0)
            FROM transacoes
            WHERE user_id = %s
              AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', NOW())
            """,
            (user_id,),
        )
        monthly_spent = float(cursor.fetchone()[0])

    if not profile and confirmed_fixed_costs == 0 and invoice_total == 0 and monthly_spent == 0:
        return (
            "Ainda não tenho informação suficiente para avaliar sua saúde financeira.\n\n"
            "Se você quiser, posso começar olhando seu extrato, suas faturas e seus limites."
        )

    current_balance = float(profile[0]) if profile and profile[0] is not None else None
    detected_income = float(profile[1]) if profile and profile[1] is not None else None
    estimated_fixed_expenses = float(profile[2]) if profile and profile[2] is not None else 0.0
    card_pressure = profile[3] if profile and profile[3] else None

    fixed_costs = confirmed_fixed_costs or estimated_fixed_expenses
    committed_base = fixed_costs + invoice_total

    if detected_income and detected_income > 0:
        commitment_ratio = committed_base / detected_income
        if commitment_ratio >= 1.0:
            status = "mais apertada"
        elif commitment_ratio >= 0.7 or card_pressure == "alta":
            status = "em atenção"
        else:
            status = "relativamente equilibrada"
    else:
        if current_balance is not None and current_balance < 0:
            status = "mais apertada"
        elif card_pressure == "alta" or committed_base >= 3000:
            status = "em atenção"
        else:
            status = "ainda inconclusiva"

    linhas = [f"Pelo que eu já tenho da sua base, sua saúde financeira hoje parece {status}."]

    if current_balance is not None:
        linhas.append(f"- saldo atual estimado: {format_brl(current_balance)}")
    if detected_income is not None:
        linhas.append(f"- renda identificada: {format_brl(detected_income)}")
    if fixed_costs > 0:
        linhas.append(f"- custos fixos considerados: {format_brl(fixed_costs)}")
    if invoice_total > 0:
        linhas.append(f"- faturas atuais somadas: {format_brl(invoice_total)}")
    if monthly_spent > 0:
        linhas.append(f"- gastos lançados neste mês: {format_brl(monthly_spent)}")

    linhas.append("")
    if status == "mais apertada":
        linhas.append("O ponto principal de atenção agora é o peso das despesas e das faturas sobre a sua margem do mês.")
    elif status == "em atenção":
        linhas.append("Você tem uma base razoável, mas vale acompanhar de perto o peso das faturas e dos compromissos fixos.")
    elif status == "relativamente equilibrada":
        linhas.append("Seu cenário parece relativamente organizado neste momento, embora eu siga monitorando sinais de pressão.")
    else:
        linhas.append("Eu já consigo ver alguns sinais, mas ainda vale completar sua base para uma leitura mais confiável.")

    return "\n".join(linhas)
