import json
import re

from openai import OpenAI

from app.config import get_settings
from app.db import get_cursor
from app.services.formatting import format_brl
from app.services.logger import get_logger

logger = get_logger("navi.financial_analysis")


# ---------------------------------------------------------------------------
# Helpers compartilhados
# ---------------------------------------------------------------------------

def _get_financial_profile(user_id: int) -> dict:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT saldo_atual_estimado, renda_identificada, despesas_fixas_estimadas, pressao_cartao
            FROM perfil_financeiro
            WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cursor.fetchone()

        cursor.execute(
            """
            SELECT COALESCE(SUM(valor_medio), 0)
            FROM custos_mensais
            WHERE user_id = %s AND tipo_custo = 'fixo' AND confirmado = TRUE
            """,
            (user_id,),
        )
        confirmed_fixed = float(cursor.fetchone()[0])

        cursor.execute(
            """
            SELECT COALESCE(SUM(valor_total), 0)
            FROM (
                SELECT DISTINCT ON (COALESCE(cartao_id, id))
                    valor_total
                FROM faturas_cartao
                WHERE user_id = %s
                ORDER BY COALESCE(cartao_id, id), created_at DESC
            ) latest
            """,
            (user_id,),
        )
        invoice_total = float(cursor.fetchone()[0])

        cursor.execute(
            """
            SELECT COALESCE(SUM(valor), 0)
            FROM transacoes
            WHERE user_id = %s
              AND tipo = 'despesa'
              AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', NOW())
            """,
            (user_id,),
        )
        monthly_spent = float(cursor.fetchone()[0])

    balance = float(row[0]) if row and row[0] is not None else None
    income = float(row[1]) if row and row[1] is not None else None
    estimated_fixed = float(row[2]) if row and row[2] is not None else 0.0
    pressure = row[3] if row and row[3] else None

    fixed_costs = confirmed_fixed or estimated_fixed

    return {
        "balance": balance,
        "income": income,
        "fixed_costs": fixed_costs,
        "invoice_total": invoice_total,
        "monthly_spent": monthly_spent,
        "card_pressure": pressure,
    }


def _parse_amount_from_text(text: str) -> float | None:
    patterns = [
        r"r\$\s*([\d.,]+)",
        r"([\d]+[.,][\d]{2})\b",
        r"\b([\d]+)\b",
    ]
    import unicodedata
    normalized = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode("ascii")
    for pattern in patterns:
        m = re.search(pattern, normalized)
        if m:
            raw = m.group(1).replace(".", "").replace(",", ".")
            try:
                val = float(raw)
                if val > 0:
                    return val
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------------------
# #24 — Posso comprar isso?
# ---------------------------------------------------------------------------

AFFORDABILITY_PATTERNS = (
    "posso comprar",
    "consigo comprar",
    "tenho como comprar",
    "da pra comprar",
    "posso pagar",
    "consigo pagar",
    "tenho dinheiro para",
    "consigo arcar",
    "tenho condicoes de comprar",
    "vale comprar",
    "posso adquirir",
)


def build_affordability_message(user_id: int, message_text: str) -> str:
    amount = _parse_amount_from_text(message_text)
    profile = _get_financial_profile(user_id)

    if amount is None:
        return (
            "Não consegui identificar o valor da compra na sua mensagem. "
            'Pode repetir com o valor? Por exemplo: "Posso comprar um celular de R$1.200?"'
        )

    income = profile["income"]
    balance = profile["balance"]
    fixed_costs = profile["fixed_costs"]
    invoice_total = profile["invoice_total"]
    monthly_spent = profile["monthly_spent"]

    committed = fixed_costs + invoice_total + monthly_spent
    available_income = (income - committed) if income else None
    available_balance = (balance - amount) if balance is not None else None

    lines = [f"Sobre comprar algo de {format_brl(amount)}:"]

    has_data = income or balance is not None

    if not has_data:
        lines.append(
            "\nAinda não tenho dados suficientes da sua base financeira para responder com segurança. "
            "Se você me enviar seu extrato ou informar sua renda, consigo te dar uma análise mais precisa."
        )
        return "\n".join(lines)

    if income:
        ratio_after = (committed + amount) / income
        if ratio_after >= 1.0:
            lines.append(
                f"\nCom base na sua renda de {format_brl(income)}, seus compromissos fixos já consomem {format_brl(committed)}. "
                f"Adicionar {format_brl(amount)} levaria seu comprometimento acima de 100% da renda — "
                "não parece um bom momento para essa compra."
            )
        elif ratio_after >= 0.8:
            lines.append(
                f"\nÉ possível, mas ficaria apertado. Sua renda é {format_brl(income)} e seus compromissos atuais somam {format_brl(committed)}. "
                f"Essa compra de {format_brl(amount)} levaria seu comprometimento para {ratio_after:.0%} da renda — vale avaliar se é urgente."
            )
        else:
            lines.append(
                f"\nParece viável. Sua renda é {format_brl(income)}, seus compromissos atuais somam {format_brl(committed)} "
                f"e ainda sobrariam {format_brl(income - committed - amount)} depois dessa compra."
            )
    elif balance is not None:
        if available_balance is not None and available_balance < 0:
            lines.append(
                f"\nSeu saldo estimado é {format_brl(balance)}, que é menor do que o valor da compra. "
                "Não parece o melhor momento para isso."
            )
        elif available_balance is not None and available_balance < amount * 0.3:
            lines.append(
                f"\nSeu saldo estimado é {format_brl(balance)}. Essa compra deixaria você com apenas "
                f"{format_brl(available_balance)} de reserva — pouco para imprevistos."
            )
        else:
            lines.append(
                f"\nSeu saldo estimado é {format_brl(balance)}. "
                f"Depois da compra, você ficaria com {format_brl(available_balance or 0)} — parece razoável."
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# #26 — Vale a pena esse empréstimo?
# ---------------------------------------------------------------------------

LOAN_PATTERNS = (
    "vale a pena esse emprestimo",
    "vale a pena o emprestimo",
    "vale a pena pegar emprestimo",
    "emprestimo de",
    "financiamento de",
    "parcelas de",
    "quero pegar emprestimo",
    "devo pegar emprestimo",
    "devo fazer emprestimo",
    "contratar emprestimo",
    "vale o emprestimo",
    "emprestimo vale",
)


def _parse_loan_details(text: str) -> tuple[float | None, float | None, int | None]:
    import unicodedata
    normalized = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode("ascii")

    total_amount: float | None = None
    monthly_payment: float | None = None
    installments: int | None = None

    # installments: "12x", "12 parcelas", "em 12"
    m = re.search(r"(\d+)\s*[xX×]\s*([\d.,]+)", normalized)
    if m:
        installments = int(m.group(1))
        raw = m.group(2).replace(".", "").replace(",", ".")
        try:
            monthly_payment = float(raw)
        except ValueError:
            pass

    m = re.search(r"(\d+)\s*parcelas?\s+de\s+(r\$\s*)?([\d.,]+)", normalized)
    if m:
        installments = int(m.group(1))
        raw = m.group(3).replace(".", "").replace(",", ".")
        try:
            monthly_payment = float(raw)
        except ValueError:
            pass

    # Total amount: "emprestimo de R$5000"
    m = re.search(r"(?:emprestimo|financiamento|credito)\s+de\s+(r\$\s*)?([\d.,]+)", normalized)
    if m:
        raw = m.group(2).replace(".", "").replace(",", ".")
        try:
            total_amount = float(raw)
        except ValueError:
            pass

    if monthly_payment and installments and not total_amount:
        total_amount = monthly_payment * installments

    return total_amount, monthly_payment, installments


def build_loan_evaluation_message(user_id: int, message_text: str) -> str:
    total_amount, monthly_payment, installments = _parse_loan_details(message_text)
    profile = _get_financial_profile(user_id)

    if not monthly_payment and not total_amount:
        return (
            "Para avaliar se o empréstimo vale a pena, preciso saber ao menos o valor das parcelas. "
            'Pode me dizer algo como "Empréstimo de R$5.000 em 12x de R$480"?'
        )

    income = profile["income"]
    fixed_costs = profile["fixed_costs"]
    invoice_total = profile["invoice_total"]
    monthly_spent = profile["monthly_spent"]
    committed = fixed_costs + invoice_total + monthly_spent

    lines = ["Sobre esse empréstimo:"]

    if total_amount:
        lines.append(f"\n- valor total: {format_brl(total_amount)}")
    if monthly_payment:
        lines.append(f"- parcela mensal: {format_brl(monthly_payment)}")
    if installments:
        lines.append(f"- prazo: {installments} meses")

    if total_amount and monthly_payment and installments:
        total_paid = monthly_payment * installments
        if total_paid > total_amount:
            custo_juros = total_paid - total_amount
            lines.append(f"- custo em juros: {format_brl(custo_juros)} ({custo_juros / total_amount:.0%} do principal)")

    lines.append("")

    if income and monthly_payment:
        new_committed_ratio = (committed + monthly_payment) / income
        if new_committed_ratio >= 1.0:
            lines.append(
                f"Com a parcela de {format_brl(monthly_payment)}, seus compromissos chegariam a {format_brl(committed + monthly_payment)}, "
                f"que é {new_committed_ratio:.0%} da sua renda de {format_brl(income)}. "
                "Esse nível de comprometimento é muito alto — recomendo evitar esse empréstimo agora."
            )
        elif new_committed_ratio >= 0.7:
            lines.append(
                f"A parcela de {format_brl(monthly_payment)} levaria seu comprometimento para {new_committed_ratio:.0%} da renda. "
                "É possível, mas deixaria pouca margem. Só vale se for realmente necessário."
            )
        else:
            lines.append(
                f"A parcela de {format_brl(monthly_payment)} representa {monthly_payment / income:.0%} da sua renda. "
                "Parece administrável — mas sempre avalie se o objetivo do empréstimo justifica o custo em juros."
            )
    elif monthly_payment:
        lines.append(
            f"A parcela mensal seria de {format_brl(monthly_payment)}. "
            "Para te dizer se vale a pena com mais precisão, seria útil saber sua renda mensal."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# #27 — Recomendações práticas
# ---------------------------------------------------------------------------

RECOMMENDATIONS_PATTERNS = (
    "como melhorar",
    "como economizar",
    "dicas financeiras",
    "recomendacoes",
    "recomendações",
    "o que fazer",
    "como organizar",
    "me aconselha",
    "me da uma dica",
    "me dá uma dica",
    "como reduzir",
    "como poupar",
    "como sair das dividas",
    "como guardar dinheiro",
    "me ajuda a melhorar",
    "o que posso fazer",
    "como melhorar minha situacao",
    "como melhorar minha situação",
)


def build_recommendations_message(user_id: int) -> str:
    settings = get_settings()
    profile = _get_financial_profile(user_id)

    balance = profile["balance"]
    income = profile["income"]
    fixed_costs = profile["fixed_costs"]
    invoice_total = profile["invoice_total"]
    monthly_spent = profile["monthly_spent"]
    pressure = profile["card_pressure"]

    has_data = income or balance is not None or fixed_costs > 0 or invoice_total > 0

    if not has_data:
        return (
            "Ainda não tenho dados suficientes para gerar recomendações personalizadas. "
            "Se você me enviar seu extrato ou fatura, ou me contar sobre sua renda e gastos, "
            "consigo te dar sugestões práticas."
        )

    context_parts = []
    if income:
        context_parts.append(f"renda mensal: {format_brl(income)}")
    if balance is not None:
        context_parts.append(f"saldo atual estimado: {format_brl(balance)}")
    if fixed_costs > 0:
        context_parts.append(f"custos fixos mensais: {format_brl(fixed_costs)}")
    if invoice_total > 0:
        context_parts.append(f"faturas de cartão somadas: {format_brl(invoice_total)}")
    if monthly_spent > 0:
        context_parts.append(f"gastos lançados este mês: {format_brl(monthly_spent)}")
    if pressure:
        context_parts.append(f"pressão de cartão: {pressure}")

    context_str = "; ".join(context_parts)

    if not settings.openai_api_key:
        logger.warning("recommendations: OPENAI_API_KEY not set, using rule-based fallback")
        return _rule_based_recommendations(profile)

    try:
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Você é o Navi, um assistente financeiro pessoal via WhatsApp. "
                        "Responda sempre em português do Brasil com tom amigável e direto. "
                        "Nunca use listas numeradas — use frases curtas em linguagem natural. "
                        "Gere exatamente 3 recomendações práticas e acionáveis baseadas nos dados fornecidos."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Com base nos dados financeiros do usuário ({context_str}), "
                        "gere 3 recomendações práticas e personalizadas para melhorar a saúde financeira. "
                        "Cada recomendação deve ser específica, acionável e baseada nos dados disponíveis. "
                        "Formato: 3 parágrafos curtos, sem numeração, sem bullets."
                    ),
                },
            ],
            max_tokens=400,
            temperature=0.7,
        )
        content = (response.choices[0].message.content or "").strip()
        if content:
            return f"Com base no que tenho aqui, três coisas que podem ajudar:\n\n{content}"
    except Exception:
        logger.exception("recommendations gpt_error user_id=%d", user_id)

    return _rule_based_recommendations(profile)


def _rule_based_recommendations(profile: dict) -> str:
    income = profile["income"]
    fixed_costs = profile["fixed_costs"]
    invoice_total = profile["invoice_total"]
    monthly_spent = profile["monthly_spent"]
    pressure = profile["card_pressure"]

    tips: list[str] = []

    if invoice_total > 0 and income and invoice_total / income >= 0.3:
        tips.append(
            f"Sua fatura de cartão ({format_brl(invoice_total)}) representa uma parcela relevante da renda. "
            "Tente pagar sempre o total — o rotativo cobra juros muito altos."
        )

    if income and fixed_costs and fixed_costs / income >= 0.5:
        tips.append(
            f"Seus custos fixos ({format_brl(fixed_costs)}) já consomem mais da metade da sua renda. "
            "Vale revisar se há algum serviço ou assinatura que pode ser cancelado ou reduzido."
        )

    if monthly_spent > 0:
        tips.append(
            f"Você já registrou {format_brl(monthly_spent)} em gastos este mês. "
            "Acompanhar esses lançamentos aqui regularmente ajuda a identificar onde cortar sem sacrifício."
        )

    if pressure == "alta":
        tips.append(
            "A pressão do seu cartão está alta. Considere usar mais débito ou Pix no dia a dia "
            "para não aumentar ainda mais o saldo da fatura."
        )

    if not tips:
        tips.append(
            "Continue registrando seus gastos regularmente — quanto mais dados eu tiver, "
            "mais precisas ficam as recomendações."
        )
        tips.append(
            "Se ainda não configurou limites de orçamento por categoria, esse é um bom próximo passo. "
            "Me diga algo como 'farmácia 300, mercado 800' para começar."
        )

    return "Com base no que tenho aqui:\n\n" + "\n\n".join(tips[:3])
