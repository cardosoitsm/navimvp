import base64
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
from app.services.onboarding import (
    DOCUMENT_ONBOARDING_PENDING,
    ONBOARDING_COMPLETE,
    get_card_names,
    set_onboarding_state,
)

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
    return f"as faturas dos seus cartoes. Podemos comecar pela do {card_names[0]}"


def document_invite_prompt(user_id: int | None = None) -> str:
    if user_id and has_document_type(user_id, "extrato"):
        invoice_target = _card_invoice_message(user_id)
        if not invoice_target:
            return (
                "Ja recebi seu extrato e, com isso, ja tenho uma boa base inicial para te acompanhar.\n\n"
                "Se quiser incluir cartoes nessa organizacao, me diga quantos cartoes voce quer cadastrar."
            )
        return (
            f"Ja recebi seu extrato, entao o proximo passo mais util e olhar {invoice_target}.\n\n"
            'Se quiser enviar agora, me responda "SIM". Se preferir deixar para depois, pode dizer "PULAR".'
        )

    return (
        "Se fizer sentido para voce, eu tambem posso olhar seu extrato ou sua fatura para entender melhor sua situacao financeira.\n\n"
        'Se quiser enviar agora, me responda "SIM". Se preferir deixar para depois, pode dizer "PULAR".'
    )


def document_upload_prompt(user_id: int | None = None) -> str:
    if user_id and has_document_type(user_id, "extrato"):
        invoice_target = _card_invoice_message(user_id)
        if not invoice_target:
            return (
                "Se quiser incluir uma fatura de cartao agora, pode me mandar por aqui.\n\n"
                'Pode ser imagem ou PDF. Se preferir deixar isso para depois, e so responder "PULAR".'
            )
        return (
            f"Perfeito. Como eu ja tenho seu extrato, agora pode me mandar {invoice_target}.\n\n"
            'Pode ser imagem ou PDF. Se mudar de ideia, e so responder "PULAR".'
        )

    return (
        "Perfeito. Pode me mandar agora um extrato da conta ou uma fatura do cartao.\n\n"
        'Pode ser imagem ou PDF. Se mudar de ideia, e so responder "PULAR".'
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
    set_onboarding_state(user_id, DOCUMENT_ONBOARDING_PENDING)


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


def register_received_document(
    user_id: int,
    media_url: str,
    media_content_type: str,
    message_text: str,
    forced_type: str | None = None,
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
    document_id = _store_document(user_id, hinted_type, media_content_type, media_url)
    return document_id, hinted_type, build_document_receipt_message(hinted_type)


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
        '"detected_income":numero ou null,'
        '"income_description":"texto" ou null,'
        '"income_confidence":"high|medium|low" ou null,'
        '"income_kind":"salary|transfer|investment|refund|unknown" ou null,'
        '"statement_rows":[{"date":"YYYY-MM-DD ou texto","description":"texto","credit":numero ou null,"debit":numero ou null,"balance":numero ou null,"raw_amount_text":"texto ou null","confidence":"high|medium|low"}],'
        '"credit_entries":[{"date":"YYYY-MM-DD ou texto","description":"texto","amount":numero}],'
        '"debit_entries":[{"date":"YYYY-MM-DD ou texto","description":"texto","amount":numero}],'
        '"estimated_fixed_expenses":numero ou null,'
        '"top_items":["item 1","item 2"]}'
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


def _persist_financial_context(user_id: int, analysis: dict[str, Any]) -> None:
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


def process_stored_document(
    document_id: int,
    user_id: int,
    media_url: str,
    media_content_type: str,
    message_text: str,
    hinted_type: str,
) -> None:
    try:
        media_bytes = _download_media_bytes(media_url)
        analysis = _extract_document_analysis(message_text, media_content_type, media_bytes, hinted_type)
    except Exception:
        analysis = None

    _update_document_analysis(document_id, analysis)

    if analysis:
        _persist_financial_context(user_id, analysis)


def build_document_receipt_message(tipo_documento: str) -> str:
    if tipo_documento == "fatura_cartao":
        detalhe = "Recebi sua fatura. Isso vai me ajudar a acompanhar melhor sua situacao."
    elif tipo_documento == "extrato":
        detalhe = "Recebi seu extrato. Isso me ajuda a entender melhor como esta sua vida financeira."
    else:
        detalhe = "Recebi seu documento. Vou guardar esse material para te ajudar melhor daqui para frente."

    return (
        f"{detalhe}\n\n"
        "Por enquanto, eu ja consigo guardar isso com seguranca e seguir com o seu acompanhamento."
    )
