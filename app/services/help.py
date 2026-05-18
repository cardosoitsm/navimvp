from app.db import get_cursor

HELP_TOPICS = [
    {
        "num": 1,
        "titulo": "Registrar despesas e receitas",
        "conteudo": (
            "Para registrar uma despesa, basta me mandar uma mensagem como:\n"
            "\"Gastei R$50 no mercado\" ou \"Paguei R$120 de farmácia\"\n\n"
            "Para receitas: \"Recebi R$3.000 de salário\"\n\n"
            "Você também pode usar voz, enviar foto de comprovante ou um PDF de extrato/fatura."
        ),
    },
    {
        "num": 2,
        "titulo": "Consultar saldo, faturas e gastos",
        "conteudo": (
            "Me pergunte diretamente:\n"
            "- \"Qual o valor da minha fatura do Nubank?\"\n"
            "- \"Quanto gastei esse mês?\"\n"
            "- \"Como está meu orçamento de alimentação?\"\n\n"
            "Posso mostrar seus últimos lançamentos, o status de cada cartão e um resumo mensal."
        ),
    },
    {
        "num": 3,
        "titulo": "Definir limites de gastos por categoria",
        "conteudo": (
            "Me mande os limites no formato:\n"
            "\"Mercado 800, farmácia 250, lazer 300\"\n\n"
            "Quando você se aproximar do limite (50%, 80% ou 100%), eu te aviso automaticamente.\n\n"
            "Para ajustar um limite existente, é só me mandar o novo valor a qualquer momento."
        ),
    },
    {
        "num": 4,
        "titulo": "Enviar extratos e faturas",
        "conteudo": (
            "Você pode me enviar:\n"
            "- Imagem (JPG/PNG) de extrato ou fatura\n"
            "- PDF do extrato bancário ou fatura do cartão\n\n"
            "Basta anexar o arquivo aqui no WhatsApp. Eu analiso o documento e registro as informações automaticamente."
        ),
    },
    {
        "num": 5,
        "titulo": "Saúde financeira e recomendações",
        "conteudo": (
            "Me pergunte:\n"
            "- \"Como está minha saúde financeira?\"\n"
            "- \"Como posso economizar?\"\n"
            "- \"Posso comprar um produto de R$2.000?\"\n\n"
            "Com base nos seus dados, dou um diagnóstico e sugestões personalizadas."
        ),
    },
    {
        "num": 6,
        "titulo": "Sobre o Navi",
        "conteudo": (
            "O Navi é seu assistente financeiro pessoal no WhatsApp.\n\n"
            "Tudo que você compartilha aqui é usado exclusivamente para te ajudar a organizar suas finanças.\n\n"
            "Para dúvidas ou sugestões, entre em contato com o suporte."
        ),
    },
]


def build_help_menu() -> str:
    linhas = ["*HelpNavi* — Em que posso te ajudar?\n"]
    for topic in HELP_TOPICS:
        linhas.append(f"{topic['num']}. {topic['titulo']}")
    linhas.append("\nResponda com o número da opção desejada.")
    return "\n".join(linhas)


def get_help_content(selection: int) -> str | None:
    for topic in HELP_TOPICS:
        if topic["num"] == selection:
            return f"*{topic['titulo']}*\n\n{topic['conteudo']}"
    return None


def save_help_context(user_id: int) -> None:
    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            UPDATE configuracoes_usuario
            SET ultimo_topico = 'help_menu', updated_at = NOW()
            WHERE user_id = %s
            """,
            (user_id,),
        )
        conn.commit()


def clear_help_context(user_id: int) -> None:
    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            UPDATE configuracoes_usuario
            SET ultimo_topico = NULL, updated_at = NOW()
            WHERE user_id = %s
            """,
            (user_id,),
        )
        conn.commit()


def is_help_menu_active(user_id: int) -> bool:
    with get_cursor() as (_, cursor):
        cursor.execute(
            "SELECT ultimo_topico FROM configuracoes_usuario WHERE user_id = %s",
            (user_id,),
        )
        row = cursor.fetchone()
    return bool(row and row[0] == "help_menu")


def parse_help_selection(text: str) -> int | None:
    stripped = text.strip()
    if stripped.isdigit():
        num = int(stripped)
        if 1 <= num <= len(HELP_TOPICS):
            return num
    return None
