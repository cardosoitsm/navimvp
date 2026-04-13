import base64
import json
import unicodedata
from typing import Any

import requests
from openai import OpenAI
from starlette.datastructures import FormData

from app.config import get_settings
from app.db import get_cursor

SKIP_DOCUMENT_WORDS = {"pular", "depois", "agora nao", "nao"}
START_DOCUMENT_WORDS = {"sim", "s", "quero", "vamos", "enviar"}
SUPPORTED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower().strip())
    return normalized.encode("ascii", "ignore").decode("ascii")


def _parse_openai_json(content: str) -> dict[str, Any]:
    payload = content.strip()
    if "```" in payload:
        parts = payload.split("```")
        if len(parts) > 1:
            payload = parts[1].replace("json", "").strip()
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("Formato inesperado retornado pela IA")
    return parsed


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _income_detection_instructions(hinted_type: str) -> str:
    if hinted_type == "extrato":
        return (
            "Para extratos bancarios, detected_income deve ser apenas a renda mais provavel identificada no documento. "
            "Considere salario, pagamento, proventos, deposito de folha, PIX recebido recorrente ou transferencia recebida com aparencia de renda. "
            "Nao use saldo atual, limite, total de entradas, transferencias entre contas do proprio usuario, estornos ou reembolsos. "
            "Se nao houver evidencias claras, retorne null. "
            "Preencha income_description com o texto mais provavel da origem da renda e income_confidence como high, medium ou low."
        )
    if hinted_type == "fatura_cartao":
        return (
            "Para faturas de cartao, detected_income normalmente deve ser null, a menos que exista alguma informacao explicita de renda no documento."
        )
    return "Se a renda nao estiver clara no documento, retorne detected_income como null."


def _format_income_confidence(confidence: str | None) -> str | None:
    mapping = {
        "high": "alta",
        "medium": "media",
        "low": "baixa",
    }
    if not confidence:
        return None
    return mapping.get(confidence, confidence)


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


def _store_document(
    user_id: int,
    tipo_documento: str,
    media_content_type: str,
    media_url: str,
) -> int:
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
            RETURNING id
            """,
            (user_id, tipo_documento, media_content_type, media_url, "recebido"),
        )
        document_id = int(cursor.fetchone()[0])
        conn.commit()
    return document_id


def _download_media_bytes(media_url: str) -> bytes:
    settings = get_settings()
    response = requests.get(
        media_url,
        auth=(settings.account_sid, settings.auth_token),
        timeout=30,
    )
    response.raise_for_status()
    return response.content


def _extract_document_analysis(
    message_text: str,
    media_content_type: str,
    media_bytes: bytes,
    hinted_type: str,
) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.openai_api_key or media_content_type not in SUPPORTED_MEDIA_TYPES:
        return None
    if media_content_type == "application/pdf":
        return None

    client = OpenAI(api_key=settings.openai_api_key)
    encoded_media = base64.b64encode(media_bytes).decode("utf-8")
    data_url = f"data:{media_content_type};base64,{encoded_media}"
    income_instructions = _income_detection_instructions(hinted_type)

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Voce analisa documentos financeiros. "
                    "Retorne apenas JSON valido com este formato: "
                    '{"document_type":"extrato|fatura_cartao|desconhecido",'
                    '"summary":"texto curto",'
                    '"current_balance":numero ou null,'
                    '"invoice_total":numero ou null,'
                    '"minimum_payment":numero ou null,'
                    '"due_date":"YYYY-MM-DD" ou null,'
                    '"issuer":"texto" ou null,'
                    '"detected_income":numero ou null,'
                    '"income_description":"texto" ou null,'
                    '"income_confidence":"high|medium|low" ou null,'
                    '"estimated_fixed_expenses":numero ou null,'
                    '"top_items":["item 1","item 2"]}'
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Mensagem do usuario: {message_text or 'sem legenda'}.\n"
                            f"Tipo sugerido inicialmente: {hinted_type}.\n"
                            f"{income_instructions}\n"
                            "Extraia apenas o que estiver visivel com boa confianca."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    },
                ],
            },
        ],
    )

    content = response.choices[0].message.content or "{}"
    analysis = _parse_openai_json(content)
    analysis["document_type"] = analysis.get("document_type") or hinted_type
    return analysis


def _update_document_analysis(document_id: int, analysis: dict[str, Any] | None) -> None:
    payload = json.dumps(analysis, ensure_ascii=False) if analysis else None
    status = "processado" if analysis else "recebido"
    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            UPDATE documentos_financeiros
            SET extracted_json = %s,
                status_processamento = %s
            WHERE id = %s
            """,
            (payload, status, document_id),
        )
        conn.commit()


def _persist_financial_context(user_id: int, analysis: dict[str, Any]) -> None:
    document_type = analysis.get("document_type")
    current_balance = _safe_float(analysis.get("current_balance"))
    detected_income = _safe_float(analysis.get("detected_income"))
    income_confidence = analysis.get("income_confidence")
    if income_confidence == "low":
        detected_income = None
    estimated_fixed_expenses = _safe_float(analysis.get("estimated_fixed_expenses"))
    invoice_total = _safe_float(analysis.get("invoice_total"))
    minimum_payment = _safe_float(analysis.get("minimum_payment"))
    due_date = analysis.get("due_date")
    issuer = analysis.get("issuer")

    pressure = None
    if invoice_total is not None:
        if detected_income and detected_income > 0:
            ratio = invoice_total / detected_income
            pressure = "alta" if ratio >= 0.5 else "moderada" if ratio >= 0.25 else "baixa"
        else:
            pressure = "moderada" if invoice_total >= 1000 else "baixa"

    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO perfil_financeiro (
                user_id,
                saldo_atual_estimado,
                renda_identificada,
                despesas_fixas_estimadas,
                pressao_cartao,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET
                saldo_atual_estimado = COALESCE(EXCLUDED.saldo_atual_estimado, perfil_financeiro.saldo_atual_estimado),
                renda_identificada = COALESCE(EXCLUDED.renda_identificada, perfil_financeiro.renda_identificada),
                despesas_fixas_estimadas = COALESCE(EXCLUDED.despesas_fixas_estimadas, perfil_financeiro.despesas_fixas_estimadas),
                pressao_cartao = COALESCE(EXCLUDED.pressao_cartao, perfil_financeiro.pressao_cartao),
                updated_at = NOW()
            """,
            (user_id, current_balance, detected_income, estimated_fixed_expenses, pressure),
        )

        if document_type == "fatura_cartao" and invoice_total is not None:
            cursor.execute(
                """
                INSERT INTO faturas_cartao (
                    user_id,
                    valor_total,
                    vencimento,
                    pagamento_minimo,
                    emissor,
                    mes_referencia
                )
                VALUES (
                    %s,
                    %s,
                    NULLIF(%s, '')::date,
                    %s,
                    %s,
                    DATE_TRUNC('month', NOW())::date
                )
                """,
                (user_id, invoice_total, due_date or "", minimum_payment, issuer),
            )

        conn.commit()


def _build_analysis_message(analysis: dict[str, Any], fallback_type: str) -> str:
    document_type = analysis.get("document_type") or fallback_type
    summary = (analysis.get("summary") or "").strip()
    current_balance = _safe_float(analysis.get("current_balance"))
    detected_income = _safe_float(analysis.get("detected_income"))
    income_description = (analysis.get("income_description") or "").strip()
    income_confidence = analysis.get("income_confidence")
    invoice_total = _safe_float(analysis.get("invoice_total"))
    minimum_payment = _safe_float(analysis.get("minimum_payment"))
    due_date = analysis.get("due_date")

    lines: list[str] = []
    if document_type == "extrato":
        lines.append("Recebi seu extrato e consegui identificar alguns sinais importantes.")
        if current_balance is not None:
            lines.append(f"- saldo estimado: R${current_balance:.2f}")
        if detected_income is not None:
            lines.append(f"- renda identificada: R${detected_income:.2f}")
            if income_description:
                lines.append(f"- origem mais provavel da renda: {income_description}")
            confidence_label = _format_income_confidence(income_confidence)
            if confidence_label:
                lines.append(f"- confianca da renda identificada: {confidence_label}")
    elif document_type == "fatura_cartao":
        lines.append("Recebi sua fatura e ja extraí alguns dados importantes.")
        if invoice_total is not None:
            lines.append(f"- valor total da fatura: R${invoice_total:.2f}")
        if due_date:
            lines.append(f"- vencimento identificado: {due_date}")
        if minimum_payment is not None:
            lines.append(f"- pagamento minimo: R${minimum_payment:.2f}")
    else:
        lines.append("Recebi seu documento financeiro e consegui registrar algumas informacoes iniciais.")

    if summary:
        lines.extend(["", summary])

    lines.extend(
        [
            "",
            "Vou usar essas informacoes para deixar meus alertas financeiros mais inteligentes.",
        ]
    )
    return "\n".join(lines)


def process_received_document(user_id: int, media_url: str, media_content_type: str, message_text: str) -> str:
    hinted_type = infer_document_type(message_text, media_content_type)
    document_id = _store_document(user_id, hinted_type, media_content_type, media_url)

    if media_content_type not in SUPPORTED_MEDIA_TYPES:
        return (
            "Recebi seu documento, mas por enquanto eu so consigo analisar imagens e PDFs. "
            "Vou guardar essa referencia para as proximas evolucoes."
        )

    try:
        media_bytes = _download_media_bytes(media_url)
        analysis = _extract_document_analysis(message_text, media_content_type, media_bytes, hinted_type)
    except Exception:
        analysis = None

    _update_document_analysis(document_id, analysis)

    if not analysis:
        return build_document_receipt_message(hinted_type)

    _persist_financial_context(user_id, analysis)
    return _build_analysis_message(analysis, hinted_type)


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
