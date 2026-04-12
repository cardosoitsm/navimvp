import unicodedata

from starlette.datastructures import FormData

from app.db import get_cursor

SKIP_DOCUMENT_WORDS = {"pular", "depois", "agora nao", "nao"}
START_DOCUMENT_WORDS = {"sim", "s", "quero", "vamos", "enviar"}


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower().strip())
    return normalized.encode("ascii", "ignore").decode("ascii")


def document_invite_prompt() -> str:
    return (
        "Se voce quiser, eu tambem posso analisar seu extrato e sua fatura para entender melhor sua situacao financeira.\n\n"
        "Quer enviar esses documentos agora? Responda SIM ou PULAR."
    )


def document_upload_prompt() -> str:
    return (
        "Perfeito. Pode me enviar agora um extrato da conta ou uma fatura do cartao.\n\n"
        "Pode ser imagem ou PDF. Se preferir deixar para depois, responda PULAR."
    )


def should_start_document_onboarding(text: str) -> bool:
    return _normalize_text(text) in START_DOCUMENT_WORDS


def should_skip_document_onboarding(text: str) -> bool:
    return _normalize_text(text) in SKIP_DOCUMENT_WORDS


def is_document_onboarding_completed(user_id: int) -> bool:
    with get_cursor() as (_, cursor):
        cursor.execute(
            "SELECT documentos_onboarding_concluido FROM configuracoes_usuario WHERE user_id = %s",
            (user_id,),
        )
        result = cursor.fetchone()
    return bool(result and result[0])


def is_waiting_for_document(user_id: int) -> bool:
    with get_cursor() as (_, cursor):
        cursor.execute(
            "SELECT aguardando_documento FROM configuracoes_usuario WHERE user_id = %s",
            (user_id,),
        )
        result = cursor.fetchone()
    return bool(result and result[0])


def start_document_onboarding(user_id: int) -> None:
    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            UPDATE configuracoes_usuario
            SET aguardando_documento = TRUE,
                documentos_onboarding_concluido = FALSE,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (user_id,),
        )
        conn.commit()


def complete_document_onboarding(user_id: int) -> None:
    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            UPDATE configuracoes_usuario
            SET aguardando_documento = FALSE,
                documentos_onboarding_concluido = TRUE,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (user_id,),
        )
        conn.commit()


def get_incoming_media(form: FormData) -> tuple[str, str] | None:
    num_media = int(form.get("NumMedia") or 0)
    if num_media <= 0:
        return None

    media_url = (form.get("MediaUrl0") or "").strip()
    media_content_type = (form.get("MediaContentType0") or "").strip()
    if not media_url or not media_content_type:
        return None
    return media_url, media_content_type


def infer_document_type(message_text: str, media_content_type: str) -> str:
    normalized = _normalize_text(message_text)

    if "fatura" in normalized or "cartao" in normalized:
        return "fatura_cartao"
    if "extrato" in normalized or "conta" in normalized:
        return "extrato"
    if media_content_type == "application/pdf":
        return "documento_pdf"
    if media_content_type.startswith("image/"):
        return "documento_imagem"
    return "documento_desconhecido"


def register_document(user_id: int, media_url: str, media_content_type: str, message_text: str) -> str:
    tipo_documento = infer_document_type(message_text, media_content_type)

    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO documentos_financeiros (
                user_id,
                tipo_documento,
                origem_midia,
                media_url,
                status_processamento
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, tipo_documento, media_content_type, media_url, "recebido"),
        )
        conn.commit()

    return tipo_documento


def build_document_receipt_message(tipo_documento: str) -> str:
    if tipo_documento == "fatura_cartao":
        detalhe = "Recebi sua fatura. Vou usar essas informacoes para melhorar meus alertas."
    elif tipo_documento == "extrato":
        detalhe = "Recebi seu extrato. Vou usar essas informacoes para entender melhor sua situacao financeira."
    else:
        detalhe = "Recebi seu documento financeiro. Vou considerar esse material nas proximas evolucoes do Navi."

    return (
        f"{detalhe}\n\n"
        "Por enquanto, eu ja consigo guardar esse documento e seguir com o seu acompanhamento financeiro."
    )
