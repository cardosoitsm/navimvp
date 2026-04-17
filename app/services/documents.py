import base64
from difflib import SequenceMatcher
import json
import re
import unicodedata
from datetime import datetime
from io import BytesIO
from typing import Any

import requests
from fastapi import HTTPException
from openai import OpenAI
from pypdf import PdfReader
from starlette.datastructures import FormData

from app.config import get_settings
from app.db import get_cursor
from app.services.formatting import format_brl, format_ptbr_date
from app.services.logger import get_logger
from app.services.onboarding import (
    CARD_INVOICE_PENDING,
    DOCUMENT_ONBOARDING_PENDING,
    ONBOARDING_COMPLETE,
    get_cards,
    get_current_card,
    get_card_names,
    set_onboarding_state,
)

logger = get_logger("navi.documents")

SKIP_DOCUMENT_WORDS = {"pular", "depois", "agora nao", "nao"}
START_DOCUMENT_WORDS = {"sim", "s", "quero", "vamos", "enviar"}
SUPPORTED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower().strip())
    return normalized.encode("ascii", "ignore").decode("ascii")


def _save_conversation_focus(user_id: int, topic: str | None, card_id: int | None = None) -> None:
    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            UPDATE configuracoes_usuario
            SET ultimo_topico = %s,
                ultimo_cartao_id = %s,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (topic, card_id, user_id),
        )
        conn.commit()


def _get_conversation_focus(user_id: int) -> tuple[str | None, int | None]:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT ultimo_topico, ultimo_cartao_id
            FROM configuracoes_usuario
            WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cursor.fetchone()
    if not row:
        return None, None
    return row[0], int(row[1]) if row[1] is not None else None


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
        pass

    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        cleaned = cleaned.replace("R$", "").replace(" ", "")
        cleaned = cleaned.replace(".", "").replace(",", ".")
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = f"-{cleaned[1:-1]}"
        try:
            return float(cleaned)
        except ValueError:
            return None

    return None


def _normalize_date(value: Any) -> str | None:
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _income_detection_instructions(hinted_type: str) -> str:
    if hinted_type == "extrato":
        return (
            "Para extratos bancarios, detected_income deve ser apenas a renda mais provavel identificada no documento. "
            "Considere salario, pagamento, proventos, deposito de folha, PIX recebido recorrente ou transferencia recebida com aparencia de renda. "
            "Nao use saldo atual, limite, total de entradas, transferencias entre contas do proprio usuario, estornos ou reembolsos. "
            "Se nao houver evidencias claras, retorne null. "
            "Diferencie salario/renda recorrente de liquidacao de investimento, resgate, PIX avulso ou transferencia pontual. "
            "Preencha income_description com o texto mais provavel da origem da renda e income_confidence como high, medium ou low. "
            f"{_statement_extraction_instructions()}"
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


def _statement_extraction_instructions() -> str:
    return (
        "Para extratos bancarios, analise a tabela usando principalmente as colunas "
        "'Data', 'Descricao', 'Credito (R$)', 'Debito (R$)' e 'Saldo (R$)'. "
        "Identifique as entradas olhando a coluna 'Credito (R$)' e correlacionando com a descricao da mesma linha. "
        "Se a descricao contiver 'LIQUIDO DE VENCIMENTO', registre essa linha em credit_entries com a descricao e o valor da coluna 'Credito (R$)'. "
        "Outra regra importante: valores sem '-' na frente representam credito/entrada; valores com '-' representam debito/saida. "
        "Preencha statement_rows como lista de objetos com {date, description, credit, debit, balance, raw_amount_text, confidence}. "
        "Para cada linha visivel, use a data da mesma linha na coluna 'Data'. "
        "Preencha credit_entries e debit_entries como listas de objetos com {date, description, amount}. "
        "Nao trate todo credito como renda: credito pode ser investimento, transferencia ou outra entrada pontual."
    )


def _normalize_statement_entries(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        return []

    normalized_entries: list[dict[str, Any]] = []
    for entry in entries[:5]:
        if not isinstance(entry, dict):
            continue
        description = str(entry.get("description") or "").strip()
        amount = _safe_float(entry.get("amount"))
        date = _normalize_date(entry.get("date"))
        if not description or amount is None:
            continue
        normalized_entries.append({"date": date, "description": description, "amount": amount})
    return normalized_entries


def _normalize_statement_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []

    normalized_rows: list[dict[str, Any]] = []
    for row in rows[:50]:
        if not isinstance(row, dict):
            continue

        description = str(row.get("description") or "").strip()
        if not description:
            continue

        credit = _safe_float(row.get("credit"))
        debit = _safe_float(row.get("debit"))
        balance = _safe_float(row.get("balance"))
        raw_amount_text = str(row.get("raw_amount_text") or "").strip()
        confidence = str(row.get("confidence") or "").strip().lower() or "medium"

        if credit is None and debit is None and raw_amount_text:
            inferred = _safe_float(raw_amount_text)
            if inferred is not None:
                if raw_amount_text.lstrip().startswith("-"):
                    debit = abs(inferred)
                else:
                    credit = inferred

        if credit is None and debit is None and balance is None:
            continue

        normalized_rows.append(
            {
                "date": _normalize_date(row.get("date")),
                "description": description,
                "credit": abs(credit) if credit is not None else None,
                "debit": abs(debit) if debit is not None else None,
                "balance": balance,
                "raw_amount_text": raw_amount_text or None,
                "confidence": confidence if confidence in {"high", "medium", "low"} else "medium",
            }
        )

    return normalized_rows


def _derive_statement_entries(statement_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    credit_entries: list[dict[str, Any]] = []
    debit_entries: list[dict[str, Any]] = []

    for row in statement_rows:
        if row.get("credit") is not None:
            credit_entries.append(
                {
                    "date": row.get("date"),
                    "description": row["description"],
                    "amount": float(row["credit"]),
                }
            )
        if row.get("debit") is not None:
            debit_entries.append(
                {
                    "date": row.get("date"),
                    "description": row["description"],
                    "amount": float(row["debit"]),
                }
            )

    return credit_entries[:10], debit_entries[:10]


def _classify_income_kind(description: str) -> str:
    normalized = _normalize_text(description)

    salary_keywords = (
        "salario",
        "folha",
        "pagamento salario",
        "proventos",
        "beneficio",
        "aposentadoria",
        "inss",
        "holerite",
        "rendimento mensal",
    )
    investment_keywords = (
        "liquido de vencimento",
        "resgate",
        "aplicacao",
        "investimento",
        "cdb",
        "tesouro",
        "renda fixa",
        "compass",
    )
    transfer_keywords = (
        "pix recebido",
        "ted recebida",
        "transferencia recebida",
        "deposito identificado",
    )
    refund_keywords = (
        "estorno",
        "reembolso",
        "devolucao",
    )

    if any(keyword in normalized for keyword in salary_keywords):
        return "salary"
    if any(keyword in normalized for keyword in investment_keywords):
        return "investment"
    if any(keyword in normalized for keyword in transfer_keywords):
        return "transfer"
    if any(keyword in normalized for keyword in refund_keywords):
        return "refund"
    return "unknown"


def _has_explicit_salary_evidence(description: str) -> bool:
    normalized = _normalize_text(description)
    strong_salary_keywords = (
        "salario",
        "folha de pagamento",
        "pagamento salario",
        "proventos",
        "deposito salario",
        "credito salario",
        "holerite",
        "inss",
        "aposentadoria",
    )
    return any(keyword in normalized for keyword in strong_salary_keywords)


def _normalize_income_signal(analysis: dict[str, Any], hinted_type: str) -> dict[str, Any]:
    if hinted_type != "extrato":
        return analysis

    income_description = (analysis.get("income_description") or "").strip()
    summary = (analysis.get("summary") or "").strip()
    combined_description = " ".join(part for part in [income_description, summary] if part).strip()
    income_kind = _classify_income_kind(combined_description)
    analysis["income_kind"] = income_kind

    if not _has_explicit_salary_evidence(combined_description):
        analysis["detected_income"] = None
        if income_kind == "salary":
            analysis["income_kind"] = "unknown"
        if not analysis.get("income_confidence"):
            analysis["income_confidence"] = "low"
        if not income_description and combined_description:
            analysis["income_description"] = combined_description
        return analysis

    if income_kind in {"investment", "transfer", "refund"}:
        analysis["detected_income"] = None
        analysis["income_confidence"] = None
        if not income_description and combined_description:
            analysis["income_description"] = combined_description
        return analysis

    if income_kind == "unknown":
        analysis["income_confidence"] = "low"
        return analysis

    if income_kind == "salary" and not analysis.get("income_confidence"):
        analysis["income_confidence"] = "high"

    return analysis


def _extract_pdf_text(media_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(media_bytes))
    pages: list[str] = []
    for page in reader.pages[:10]:
        text = page.extract_text() or ""
        text = re.sub(r"\s+\n", "\n", text)
        pages.append(text.strip())
    return "\n\n".join(part for part in pages if part).strip()


def has_document_type(user_id: int, tipo_documento: str) -> bool:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT 1
            FROM documentos_financeiros
            WHERE user_id = %s
              AND tipo_documento = %s
            LIMIT 1
            """,
            (user_id, tipo_documento),
        )
        return cursor.fetchone() is not None


def _card_invoice_message(user_id: int) -> str:
    card_names = get_card_names(user_id)
    if not card_names:
        return ""
    if len(card_names) == 1:
        return f"a fatura do {card_names[0]}"
    return f"as faturas dos seus cartões. Podemos começar pela do {card_names[0]}"


def document_invite_prompt(user_id: int | None = None) -> str:
    if user_id and has_document_type(user_id, "extrato"):
        invoice_target = _card_invoice_message(user_id)
        if not invoice_target:
            return (
                "Já recebi seu extrato e, com isso, já tenho uma boa base inicial para te acompanhar.\n\n"
                "Se quiser incluir cartões nessa organização, me diga quantos cartões você quer cadastrar."
            )
        return (
            f"Já recebi seu extrato, então o próximo passo mais útil é olhar {invoice_target}.\n\n"
            'Se quiser enviar agora, me responda "SIM". Se preferir deixar para depois, pode dizer "PULAR".'
        )

    return (
        "Se fizer sentido para você, eu também posso olhar seu extrato ou sua fatura para entender melhor sua situação financeira.\n\n"
        'Se quiser enviar agora, me responda "SIM". Se preferir deixar para depois, pode dizer "PULAR".'
    )


def document_upload_prompt(user_id: int | None = None) -> str:
    if user_id:
        current_card = get_current_card(user_id)
        if current_card:
            return (
                f"Perfeito. Agora pode me mandar a fatura atual do {current_card['nome_cartao']}.\n\n"
                'Pode ser imagem ou PDF. Se preferir pular esta fatura por enquanto, responda "PULAR".'
            )

    if user_id and has_document_type(user_id, "extrato"):
        invoice_target = _card_invoice_message(user_id)
        if not invoice_target:
            return (
                "Se quiser incluir uma fatura de cartao agora, pode me mandar por aqui.\n\n"
                'Pode ser imagem ou PDF. Se preferir deixar isso para depois, e so responder "PULAR".'
            )
        return (
            f"Perfeito. Como eu já tenho seu extrato, agora pode me mandar {invoice_target}.\n\n"
            'Pode ser imagem ou PDF. Se mudar de ideia, e so responder "PULAR".'
        )

    return (
        "Perfeito. Pode me mandar agora um extrato da conta ou uma fatura do cartão.\n\n"
        'Pode ser imagem ou PDF. Se mudar de ideia, e so responder "PULAR".'
    )


def document_upload_retry_prompt(card_name: str | None = None) -> str:
    if card_name:
        return (
            f"Ainda aguardo a fatura do {card_name}. "
            'Pode enviar como imagem ou PDF. Para pular, responda "PULAR".'
        )
    return (
        "Ainda aguardo o documento. "
        'Pode enviar como imagem ou PDF. Para pular, responda "PULAR".'
    )


def document_invite_retry_prompt() -> str:
    return (
        'Pode me responder "SIM" para enviar um documento agora '
        'ou "PULAR" para deixar essa etapa para depois.'
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
    set_onboarding_state(user_id, CARD_INVOICE_PENDING)


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
    set_onboarding_state(user_id, ONBOARDING_COMPLETE)


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
    card_id: int | None = None,
) -> int:
    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO documentos_financeiros (
                user_id,
                tipo_documento,
                origem_midia,
                media_url,
                status_processamento,
                cartao_id
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, tipo_documento, media_content_type, media_url, "recebido", card_id),
        )
        document_id = int(cursor.fetchone()[0])
        conn.commit()
    return document_id


def register_received_document(
    user_id: int,
    media_url: str,
    media_content_type: str,
    message_text: str,
    forced_type: str | None = None,
    card_id: int | None = None,
) -> tuple[int, str, str]:
    if media_content_type not in SUPPORTED_MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Recebi sua midia, mas por enquanto consigo trabalhar apenas com imagem ou PDF. "
                "Pode me enviar o extrato ou a fatura nesses formatos?"
            ),
        )

    hinted_type = forced_type or infer_document_type(message_text, media_content_type)
    document_id = _store_document(user_id, hinted_type, media_content_type, media_url, card_id)
    return document_id, hinted_type, build_document_receipt_message(hinted_type)


def _find_best_card_match(user_id: int, message_text: str) -> dict[str, Any] | None:
    normalized_message = _normalize_text(message_text)
    cards = get_cards(user_id)
    if not cards:
        return None

    best_card: dict[str, Any] | None = None
    best_score = 0.0
    for card in cards:
        card_name = str(card.get("nome_cartao") or "").strip()
        if not card_name:
            continue
        normalized_name = _normalize_text(card_name)
        score = 0.0
        if normalized_name and normalized_name in normalized_message:
            score += 4.0

        tokens = [token for token in re.split(r"\s+", normalized_name) if len(token) > 2]
        score += sum(1.0 for token in tokens if token in normalized_message)
        score += SequenceMatcher(None, normalized_name, normalized_message).ratio()

        if score > best_score:
            best_score = score
            best_card = card

    return best_card if best_score >= 1.2 else None


def _load_invoice_from_recent_documents(user_id: int, selected_card: dict[str, Any]) -> tuple[float | None, Any, float | None] | None:
    cards = get_cards(user_id)
    if not cards:
        return None

    selected_order = int(selected_card.get("ordem") or 0)
    if selected_order <= 0:
        return None

    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT extracted_json
            FROM documentos_financeiros
            WHERE user_id = %s
              AND tipo_documento = 'fatura_cartao'
              AND extracted_json IS NOT NULL
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (user_id, max(len(cards), selected_order)),
        )
        rows = cursor.fetchall()

    if not rows:
        return None

    ordered_payloads = list(reversed(rows))
    index = selected_order - 1
    if index >= len(ordered_payloads):
        return None

    try:
        payload = json.loads(ordered_payloads[index][0])
    except (TypeError, json.JSONDecodeError, IndexError):
        return None

    if not isinstance(payload, dict):
        return None

    invoice_total = _safe_float(payload.get("invoice_total"))
    if invoice_total is None:
        return None

    due_date = payload.get("due_date")
    minimum_payment = _safe_float(payload.get("minimum_payment"))
    return invoice_total, due_date, minimum_payment


def build_invoice_status_message(user_id: int, message_text: str) -> str:
    cards = get_cards(user_id)
    if not cards:
        return "Ainda não encontrei cartões cadastrados por aqui. Se quiser, eu posso te ajudar a cadastrar seus cartões primeiro."

    selected_card = _find_best_card_match(user_id, message_text)
    if not selected_card and len(cards) == 1:
        selected_card = cards[0]

    if not selected_card:
        card_names = ", ".join(str(card["nome_cartao"]) for card in cards[:4])
        return (
            "Consigo sim. Só me diga de qual cartão você quer consultar a fatura.\n\n"
            f"Hoje eu tenho estes cadastrados por aqui: {card_names}."
        )

    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT valor_total, vencimento, pagamento_minimo
            FROM faturas_cartao
            WHERE user_id = %s
              AND cartao_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, selected_card["id"]),
        )
        row = cursor.fetchone()
        if not row:
            normalized_name = _normalize_text(str(selected_card["nome_cartao"]))
            tokens = [token for token in re.split(r"\s+", normalized_name) if len(token) > 3]
            for token in tokens:
                cursor.execute(
                    """
                    SELECT valor_total, vencimento, pagamento_minimo
                    FROM faturas_cartao
                    WHERE user_id = %s
                      AND emissor IS NOT NULL
                      AND LOWER(emissor) LIKE %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (user_id, f"%{token}%"),
                )
                row = cursor.fetchone()
                if row:
                    break

    card_name = str(selected_card["nome_cartao"])
    _save_conversation_focus(
        user_id,
        "invoice_status",
        int(selected_card["id"]) if selected_card.get("id") is not None else None,
    )

    if not row:
        document_fallback = _load_invoice_from_recent_documents(user_id, selected_card)
        if document_fallback:
            valor_total, vencimento, pagamento_minimo = document_fallback
            resposta = [f"A última fatura que tenho salva do {card_name} está em {format_brl(valor_total)}."]
            vencimento_formatado = format_ptbr_date(vencimento)
            if vencimento_formatado:
                resposta.append(f"O vencimento identificado é {vencimento_formatado}.")
            if pagamento_minimo is not None:
                resposta.append(f"O pagamento mínimo dela ficou em {format_brl(pagamento_minimo)}.")
            return "\n\n".join(resposta)

        return (
            f"Ainda não encontrei uma fatura salva para o {card_name}.\n\n"
            f"Se quiser, pode me mandar a fatura atual do {card_name} que eu organizo isso por aqui."
        )

    valor_total = float(row[0])
    vencimento = row[1]
    pagamento_minimo = float(row[2]) if row[2] is not None else None

    resposta = [f"A última fatura que tenho salva do {card_name} está em {format_brl(valor_total)}."]
    vencimento_formatado = _format_ptbr_date(vencimento)
    if vencimento_formatado:
        resposta.append(f"O vencimento identificado é {vencimento_formatado}.")
    if pagamento_minimo is not None:
        resposta.append(f"O pagamento mínimo dela ficou em {format_brl(pagamento_minimo)}.")
    return "\n\n".join(resposta)


def is_invoice_followup_message(user_id: int, message_text: str) -> bool:
    normalized = _normalize_text(message_text)
    if not normalized:
        return False

    last_topic, _ = _get_conversation_focus(user_id)
    if last_topic != "invoice_status":
        return False

    if not _find_best_card_match(user_id, message_text):
        return False

    followup_starts = (
        "e do ",
        "e da ",
        "e o do ",
        "e a do ",
        "e o ",
        "e a ",
        "do ",
        "da ",
    )
    return normalized.startswith(followup_starts) or len(normalized.split()) <= 6


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

    client = OpenAI(api_key=settings.openai_api_key)
    income_instructions = _income_detection_instructions(hinted_type)
    system_content = (
        "Voce analisa documentos financeiros. "
        "Retorne apenas JSON valido com este formato: "
        '{"document_type":"extrato|fatura_cartao|desconhecido",'
        '"summary":"texto curto",'
        '"current_balance":numero ou null,'
        '"invoice_total":numero ou null,'
        '"minimum_payment":numero ou null,'
        '"due_date":"YYYY-MM-DD" ou null,'
        '"issuer":"texto" ou null,'
        '"credit_limit":numero ou null,'
        '"best_purchase_day":numero inteiro entre 1 e 31 ou null,'
        '"detected_income":numero ou null,'
        '"income_description":"texto" ou null,'
        '"income_confidence":"high|medium|low" ou null,'
        '"income_kind":"salary|transfer|investment|refund|unknown" ou null,'
        '"statement_rows":[{"date":"YYYY-MM-DD ou texto","description":"texto","credit":numero ou null,"debit":numero ou null,"balance":numero ou null,"raw_amount_text":"texto ou null","confidence":"high|medium|low"}],'
        '"credit_entries":[{"date":"YYYY-MM-DD ou texto","description":"texto","amount":numero}],'
        '"debit_entries":[{"date":"YYYY-MM-DD ou texto","description":"texto","amount":numero}],'
        '"estimated_fixed_expenses":numero ou null,'
        '"top_items":["item 1","item 2"]}. '
        "Para faturas de cartao: credit_limit e o limite total do cartao (campo 'limite', 'limite do cartao' ou similar). "
        "best_purchase_day e o melhor dia para compras (campo 'melhor dia para compras', 'data de fechamento' menos alguns dias, ou similar). "
        "Se nao estiver explicito, retorne null."
    )

    if media_content_type == "application/pdf":
        extracted_text = _extract_pdf_text(media_bytes)
        if not extracted_text:
            return None

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": (
                        f"Mensagem do usuario: {message_text or 'sem legenda'}.\n"
                        f"Tipo sugerido inicialmente: {hinted_type}.\n"
                        f"{income_instructions}\n"
                        "O texto abaixo foi extraido de um PDF. Reconstrua as linhas relevantes do documento e preencha statement_rows quando for extrato.\n\n"
                        f"{extracted_text[:18000]}"
                    ),
                },
            ],
        )
    else:
        encoded_media = base64.b64encode(media_bytes).decode("utf-8")
        data_url = f"data:{media_content_type};base64,{encoded_media}"

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Mensagem do usuario: {message_text or 'sem legenda'}.\n"
                                f"Tipo sugerido inicialmente: {hinted_type}.\n"
                                f"{income_instructions}\n"
                                "Extraia apenas o que estiver visivel com boa confianca. Quando for extrato, preencha statement_rows linha a linha."
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
    analysis["statement_rows"] = _normalize_statement_rows(analysis.get("statement_rows"))
    derived_credit_entries, derived_debit_entries = _derive_statement_entries(analysis["statement_rows"])
    analysis["credit_entries"] = derived_credit_entries or _normalize_statement_entries(analysis.get("credit_entries"))
    analysis["debit_entries"] = derived_debit_entries or _normalize_statement_entries(analysis.get("debit_entries"))
    return _normalize_income_signal(analysis, hinted_type)


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


def _persist_financial_context(user_id: int, analysis: dict[str, Any], card_id: int | None = None) -> None:
    document_type = analysis.get("document_type")
    current_balance = _safe_float(analysis.get("current_balance"))
    detected_income = _safe_float(analysis.get("detected_income"))
    income_confidence = analysis.get("income_confidence")
    income_kind = analysis.get("income_kind")
    if income_confidence == "low" or income_kind in {"investment", "transfer", "refund", "unknown"}:
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
                    cartao_id,
                    valor_total,
                    vencimento,
                    pagamento_minimo,
                    emissor,
                    mes_referencia
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    NULLIF(%s, '')::date,
                    %s,
                    %s,
                    DATE_TRUNC('month', NOW())::date
                )
                """,
                (user_id, card_id, invoice_total, due_date or "", minimum_payment, issuer),
            )

        if document_type == "fatura_cartao" and card_id is not None:
            credit_limit = _safe_float(analysis.get("credit_limit"))
            best_purchase_day_raw = analysis.get("best_purchase_day")
            best_purchase_day = None
            if best_purchase_day_raw is not None:
                try:
                    day = int(best_purchase_day_raw)
                    if 1 <= day <= 31:
                        best_purchase_day = day
                except (TypeError, ValueError):
                    pass

            if credit_limit is not None or best_purchase_day is not None:
                cursor.execute(
                    """
                    UPDATE cartoes_usuario
                    SET dia_melhor_compra = COALESCE(%s, dia_melhor_compra),
                        limite_credito    = COALESCE(%s, limite_credito)
                    WHERE id = %s
                    """,
                    (best_purchase_day, credit_limit, card_id),
                )

        conn.commit()


def _build_analysis_message(analysis: dict[str, Any], fallback_type: str) -> str:
    document_type = analysis.get("document_type") or fallback_type
    summary = (analysis.get("summary") or "").strip()
    current_balance = _safe_float(analysis.get("current_balance"))
    detected_income = _safe_float(analysis.get("detected_income"))
    income_description = (analysis.get("income_description") or "").strip()
    income_confidence = analysis.get("income_confidence")
    income_kind = analysis.get("income_kind")
    statement_rows = _normalize_statement_rows(analysis.get("statement_rows"))
    credit_entries = _normalize_statement_entries(analysis.get("credit_entries"))
    debit_entries = _normalize_statement_entries(analysis.get("debit_entries"))
    invoice_total = _safe_float(analysis.get("invoice_total"))
    minimum_payment = _safe_float(analysis.get("minimum_payment"))
    due_date = analysis.get("due_date")

    lines: list[str] = []
    if document_type == "extrato":
        lines.append("Recebi seu extrato e consegui identificar alguns sinais importantes.")
        if statement_rows:
            lines.append(f"- linhas financeiras identificadas: {len(statement_rows)}")
        if current_balance is not None:
            lines.append(f"- saldo estimado: R${current_balance:.2f}")
        if detected_income is not None:
            lines.append(f"- renda identificada: R${detected_income:.2f}")
            if income_description:
                lines.append(f"- origem mais provavel da renda: {income_description}")
            confidence_label = _format_income_confidence(income_confidence)
            if confidence_label:
                lines.append(f"- confianca da renda identificada: {confidence_label}")
        elif income_kind in {"investment", "transfer", "refund"} and income_description:
            labels = {
                "investment": "uma movimentacao de investimento",
                "transfer": "uma transferencia recebida",
                "refund": "um estorno ou reembolso",
            }
            lines.append(f"- encontrei um credito, mas ele parece ser {labels.get(income_kind, 'um credito pontual')} e nao renda recorrente")
            lines.append(f"- origem observada: {income_description}")
        if credit_entries:
            lines.extend(["", "Creditos identificados no extrato:"])
            for entry in credit_entries[:3]:
                prefix = f"{entry['date']} - " if entry.get("date") else ""
                lines.append(f"- {prefix}{entry['description']}: R${entry['amount']:.2f}")
        if debit_entries:
            lines.extend(["", "Debitos identificados no extrato:"])
            for entry in debit_entries[:3]:
                prefix = f"{entry['date']} - " if entry.get("date") else ""
                lines.append(f"- {prefix}{entry['description']}: R${entry['amount']:.2f}")
    elif document_type == "fatura_cartao":
        lines.append("Recebi sua fatura e ja extraí alguns dados importantes.")
        if invoice_total is not None:
            lines.append(f"- valor total da fatura: {format_brl(invoice_total)}")
        if due_date:
            formatted_due = format_ptbr_date(due_date) or due_date
            lines.append(f"- vencimento: {formatted_due}")
        if minimum_payment is not None:
            lines.append(f"- pagamento minimo: {format_brl(minimum_payment)}")
        credit_limit = _safe_float(analysis.get("credit_limit"))
        if credit_limit is not None:
            lines.append(f"- limite do cartao: {format_brl(credit_limit)}")
        best_purchase_day_raw = analysis.get("best_purchase_day")
        if best_purchase_day_raw is not None:
            try:
                day = int(best_purchase_day_raw)
                if 1 <= day <= 31:
                    lines.append(f"- melhor dia para compras: dia {day}")
            except (TypeError, ValueError):
                pass
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
            "Recebi seu documento, mas por enquanto eu só consigo analisar imagens e PDFs. "
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


def process_stored_document(
    document_id: int,
    user_id: int,
    media_url: str,
    media_content_type: str,
    message_text: str,
    hinted_type: str,
    card_id: int | None = None,
) -> None:
    logger.info("doc_processing_start document_id=%d user_id=%d type=%s", document_id, user_id, hinted_type)
    try:
        media_bytes = _download_media_bytes(media_url)
        analysis = _extract_document_analysis(message_text, media_content_type, media_bytes, hinted_type)
    except Exception:
        logger.exception("doc_processing_error document_id=%d user_id=%d", document_id, user_id)
        analysis = None

    _update_document_analysis(document_id, analysis)

    if analysis:
        _persist_financial_context(user_id, analysis, card_id)
        logger.info("doc_processing_done document_id=%d user_id=%d", document_id, user_id)
    else:
        logger.warning("doc_processing_no_analysis document_id=%d user_id=%d", document_id, user_id)


def build_document_receipt_message(tipo_documento: str) -> str:
    if tipo_documento == "fatura_cartao":
        detalhe = "Recebi sua fatura. Isso vai me ajudar a acompanhar melhor sua situação."
    elif tipo_documento == "extrato":
        detalhe = "Recebi seu extrato. Isso me ajuda a entender melhor como está sua vida financeira."
    else:
        detalhe = "Recebi seu documento. Vou guardar esse material para te ajudar melhor daqui para frente."

    return (
        f"{detalhe}\n\n"
        "Por enquanto, eu já consigo guardar isso com segurança e seguir com o seu acompanhamento."
    )


def _load_recent_invoice_document_state_v2(user_id: int, selected_card: dict[str, Any]) -> dict[str, Any] | None:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT extracted_json, status_processamento, created_at
            FROM documentos_financeiros
            WHERE user_id = %s
              AND tipo_documento = 'fatura_cartao'
              AND cartao_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, selected_card["id"]),
        )
        direct_row = cursor.fetchone()
        if direct_row:
            extracted_json, status, created_at = direct_row
            payload = None
            if extracted_json:
                try:
                    parsed = json.loads(extracted_json)
                    if isinstance(parsed, dict):
                        payload = parsed
                except (TypeError, json.JSONDecodeError):
                    payload = None
            return {
                "payload": payload,
                "status": str(status or "").strip().lower(),
                "created_at": created_at,
            }

    cards = get_cards(user_id)
    if not cards:
        return None

    selected_order = int(selected_card.get("ordem") or 0)
    if selected_order <= 0:
        return None

    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT extracted_json, status_processamento, created_at
            FROM documentos_financeiros
            WHERE user_id = %s
              AND tipo_documento = 'fatura_cartao'
            ORDER BY created_at ASC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()

    if not rows:
        return None

    card_name = _normalize_text(str(selected_card.get("nome_cartao") or ""))
    card_tokens = [token for token in re.split(r"\s+", card_name) if len(token) > 2]
    parsed_rows: list[dict[str, Any]] = []
    for extracted_json, status, created_at in rows:
        payload = None
        if extracted_json:
            try:
                parsed = json.loads(extracted_json)
                if isinstance(parsed, dict):
                    payload = parsed
            except (TypeError, json.JSONDecodeError):
                payload = None

        parsed_rows.append(
            {
                "payload": payload,
                "status": str(status or "").strip().lower(),
                "created_at": created_at,
            }
        )

    for item in reversed(parsed_rows):
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        issuer = _normalize_text(str(payload.get("issuer") or ""))
        summary = _normalize_text(str(payload.get("summary") or ""))
        searchable = " ".join(part for part in [issuer, summary] if part).strip()
        if searchable and any(token in searchable for token in card_tokens):
            return item

    recent_batch = parsed_rows[-len(cards) :]
    index = selected_order - 1
    if 0 <= index < len(recent_batch):
        return recent_batch[index]

    return None


def _build_invoice_status_response_v2(
    card_name: str,
    valor_total: float,
    vencimento: Any,
    pagamento_minimo: float | None,
) -> str:
    resposta = [f"A última fatura que tenho salva do {card_name} está em {format_brl(valor_total)}."]
    vencimento_formatado = format_ptbr_date(vencimento)
    if vencimento_formatado:
        resposta.append(f"O vencimento identificado é {vencimento_formatado}.")
    if pagamento_minimo is not None:
        resposta.append(f"O pagamento mínimo dela ficou em {format_brl(pagamento_minimo)}.")
    return "\n\n".join(resposta)


def build_invoice_status_message(user_id: int, message_text: str) -> str:
    cards = get_cards(user_id)
    if not cards:
        return "Ainda não encontrei cartões cadastrados por aqui. Se quiser, eu posso te ajudar a cadastrar seus cartões primeiro."

    selected_card = _find_best_card_match(user_id, message_text)
    if not selected_card and len(cards) == 1:
        selected_card = cards[0]

    if not selected_card:
        card_names = ", ".join(str(card["nome_cartao"]) for card in cards[:4])
        return (
            "Consigo sim. Só me diga de qual cartão você quer consultar a fatura.\n\n"
            f"Hoje eu tenho estes cadastrados por aqui: {card_names}."
        )

    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT valor_total, vencimento, pagamento_minimo
            FROM faturas_cartao
            WHERE user_id = %s
              AND cartao_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, selected_card["id"]),
        )
        row = cursor.fetchone()
        if not row:
            normalized_name = _normalize_text(str(selected_card["nome_cartao"]))
            tokens = [token for token in re.split(r"\s+", normalized_name) if len(token) > 3]
            for token in tokens:
                cursor.execute(
                    """
                    SELECT valor_total, vencimento, pagamento_minimo
                    FROM faturas_cartao
                    WHERE user_id = %s
                      AND emissor IS NOT NULL
                      AND LOWER(emissor) LIKE %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (user_id, f"%{token}%"),
                )
                row = cursor.fetchone()
                if row:
                    break

    card_name = str(selected_card["nome_cartao"])
    _save_conversation_focus(
        user_id,
        "invoice_status",
        int(selected_card["id"]) if selected_card.get("id") is not None else None,
    )

    if not row:
        document_fallback = _load_invoice_from_recent_documents(user_id, selected_card)
        if document_fallback:
            valor_total, vencimento, pagamento_minimo = document_fallback
            return _build_invoice_status_response_v2(card_name, valor_total, vencimento, pagamento_minimo)

        document_state = _load_recent_invoice_document_state_v2(user_id, selected_card)
        if document_state:
            payload = document_state.get("payload")
            if isinstance(payload, dict):
                invoice_total = _safe_float(payload.get("invoice_total"))
                if invoice_total is not None:
                    due_date = payload.get("due_date")
                    minimum_payment = _safe_float(payload.get("minimum_payment"))
                    return _build_invoice_status_response_v2(card_name, invoice_total, due_date, minimum_payment)

            status = str(document_state.get("status") or "").strip().lower()
            if status in {"recebido", "processando"}:
                return (
                    f"Eu já recebi a fatura do {card_name} e ainda estou terminando de analisar esse arquivo.\n\n"
                    "Se quiser, me chama de novo em instantes que eu te devolvo os detalhes."
                )

        with get_cursor() as (_, cursor):
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM documentos_financeiros
                WHERE user_id = %s
                  AND tipo_documento = 'fatura_cartao'
                """,
                (user_id,),
            )
            any_invoice_count = cursor.fetchone()

        if any_invoice_count and int(any_invoice_count[0] or 0) > 0:
            return (
                f"Eu já recebi pelo menos uma fatura por aqui, mas ainda não consegui associar com segurança a do {card_name}.\n\n"
                "Se quiser, me chama de novo em instantes. Se isso continuar, eu ajusto essa associação com você."
            )

        return (
            f"Ainda não encontrei uma fatura salva para o {card_name}.\n\n"
            f"Se quiser, pode me mandar a fatura atual do {card_name} que eu organizo isso por aqui."
        )

    valor_total = float(row[0])
    vencimento = row[1]
    pagamento_minimo = float(row[2]) if row[2] is not None else None
    return _build_invoice_status_response_v2(card_name, valor_total, vencimento, pagamento_minimo)
