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
from app.services.users import get_user_locale
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


_WRAPPER_ARRAY_KEYS = ("adjustments", "ajustes", "items", "data", "rows", "lancamentos", "result", "results")


def _parse_openai_json_list(content: str) -> list[dict[str, Any]]:
    """Robust parser that always returns a list, handling GPT response variations."""
    payload = content.strip()

    # Strip markdown fences
    md_match = re.search(r"```(?:json)?\s*(.*?)\s*```", payload, re.DOTALL)
    if md_match:
        payload = md_match.group(1).strip()
    elif "```" in payload:
        parts = payload.split("```")
        if len(parts) > 1:
            payload = parts[1].replace("json", "", 1).strip()

    # Extract JSON boundaries — find the outermost [ ] or { }
    first_bracket = next((i for i, c in enumerate(payload) if c in ("[", "{")), None)
    if first_bracket is not None:
        opener = payload[first_bracket]
        closer = "]" if opener == "[" else "}"
        last_bracket = len(payload) - 1 - next(
            (i for i, c in enumerate(reversed(payload)) if c == closer), -1
        )
        if last_bracket >= first_bracket:
            payload = payload[first_bracket : last_bracket + 1]

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("parse_json_failed raw_content=%.500s", content)
        return []

    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]

    if isinstance(parsed, dict):
        for key in _WRAPPER_ARRAY_KEYS:
            val = parsed.get(key)
            if isinstance(val, list):
                return [item for item in val if isinstance(item, dict)]
        if len(parsed) == 1:
            only_val = next(iter(parsed.values()))
            if isinstance(only_val, list):
                return [item for item in only_val if isinstance(item, dict)]

    logger.warning("parse_json_failed raw_content=%.500s", content)
    return []


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
            "ATENCAO: detected_income e credit_entries sao campos completamente independentes. "
            "Mesmo que um credito NAO seja renda (ex: remuneracao de aplicacao automatica, rendimento, estorno, transferencia pontual), "
            "ele DEVE aparecer em credit_entries. Apenas detected_income fica null nesses casos. "
            "Preencha income_description com o texto mais provavel da origem da renda e income_confidence como high, medium ou low. "
            f"{_summary_page_instructions()}"
            f"{_statement_extraction_instructions()}"
        )
    if hinted_type == "fatura_cartao":
        return (
            "Para faturas de cartao, detected_income normalmente deve ser null, a menos que exista alguma informacao explicita de renda no documento."
        )
    # Generic PDF/image: may be a bank statement — include full extraction instructions
    return (
        "Se a renda nao estiver clara no documento, retorne detected_income como null. "
        f"{_statement_extraction_instructions()}"
    )


def _format_income_confidence(confidence: str | None) -> str | None:
    mapping = {
        "high": "alta",
        "medium": "media",
        "low": "baixa",
    }
    if not confidence:
        return None
    return mapping.get(confidence, confidence)


def _summary_page_instructions() -> str:
    return (
        "Se o documento contiver uma pagina de resumo financeiro "
        "(ex: 'RESUMO DO EXTRATO', 'DEMONSTRATIVO', 'POSICAO DA CONTA', 'RESUMO DA CONTA'), "
        "extraia tambem os seguintes campos: "
        "current_balance: saldo atual da conta corrente (campo 'SALDO', 'Saldo Atual', 'Saldo Disponivel'); "
        "account_limit: limite da conta corrente (campo 'Limite', 'Limite da Conta', 'Limite Disponivel', 'Limite Total'); "
        "pending_charges: provisao total de encargos — some juros e IOF se estiverem separados "
        "(campos 'Provisao de Juros', 'Provisao de IOF', 'Encargos a Debitar', 'Juros + IOF'); "
        "charges_debit_date: data prevista de debito dos encargos no formato YYYY-MM-DD "
        "(campo 'Data de Debito', 'Debito em', 'Vencimento dos Encargos', 'Data do Debito dos Juros'). "
        "Se juros e IOF tiverem datas separadas, use a mais proxima. "
        "Esses campos devem ser preenchidos mesmo que a pagina de resumo seja diferente da pagina de lancamentos."
    )


def _statement_extraction_instructions() -> str:
    return (
        "REGRA FUNDAMENTAL — retorne TODOS os lancamentos encontrados no texto, sem nenhuma excecao. "
        "Nao filtre por tipo, valor, categoria ou natureza. "
        "Creditos, debitos, valores minimos (ex: R$0,02), lancamentos sem categoria clara — TODOS devem estar em statement_rows. "
        "Para cada linha com data visivel no texto, crie uma entrada em statement_rows. "
        "Para extratos bancarios, use as colunas 'Data', 'Descricao', 'Credito (R$)', 'Debito (R$)' e 'Saldo (R$)' (ou equivalentes). "
        "Valores positivos sem sinal sao credito; valores com '-' ou na coluna de debito sao saidas. "
        "REGRA DE DIRECAO: descricoes com 'DEBITO VISA ELECTRON', 'DEBITO MASTERCARD', 'DEBITO ELO' "
        "sao sempre saidas (coluna debito) — nunca as coloque em credit_entries. "
        "REGRA DE INDEPENDENCIA: credit_entries NAO eh filtrado por detected_income. "
        "Um lancamento de 'REMUNERACAO APLICACAO AUTOMATICA', 'RENDIMENTO', 'RESGATE' ou qualquer investimento "
        "DEVE constar em credit_entries independentemente — apenas detected_income fica null nesses casos. "
        "Para cada entrada em statement_rows use os campos: "
        "date, description (exatamente como no documento), credit, debit, balance, raw_amount_text, confidence, "
        "tipo_custo ('fixo' para recorrentes mensais, 'variavel' para gastos pontuais, "
        "'rendimento' para creditos de investimento/aplicacao, 'credito' para outros creditos), "
        "categoria_sugerida (use uma das categorias disponiveis ou 'Outros'), "
        "subcategoria_sugerida (subcategoria especifica quando identificavel, ex: 'supermercado', 'uber', 'academia', ou null), "
        "confirmado (sempre false na primeira analise). "
        "debit_entries: TODOS os debitos — compras, PIX enviados, tarifas, qualquer saida. "
        "credit_entries: TODOS os creditos — rendimentos, aplicacoes, estornos, transferencias, qualquer entrada."
    )


def _normalize_statement_entries(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        return []

    normalized_entries: list[dict[str, Any]] = []
    for entry in entries[:200]:
        if not isinstance(entry, dict):
            continue
        description = str(entry.get("description") or "").strip()
        amount = _safe_float(entry.get("amount"))
        date = _normalize_date(entry.get("date"))
        if not description or amount is None:
            continue
        normalized_entries.append({"date": date, "description": description, "amount": amount})
    return normalized_entries


_NON_TRANSACTION_EXACT = frozenset({
    "total",
    "subtotal",
    "saldo",
})

_NON_TRANSACTION_PREFIXES = (
    "saldo anterior",
    "saldo atual",
    "saldo disponivel",
    "saldo do periodo",
    "saldo do dia",
    "saldo inicial",
    "saldo final",
    "saldo em ",
    "saldo periodo",
)


def _ascii_lower(text: str) -> str:
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode("ascii")


def _is_non_transactional(description: str) -> bool:
    d = _ascii_lower(description)
    if d in _NON_TRANSACTION_EXACT:
        return True
    return any(d.startswith(prefix) for prefix in _NON_TRANSACTION_PREFIXES)


def _normalize_statement_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []

    normalized_rows: list[dict[str, Any]] = []
    filtered_count = 0
    for row in rows[:200]:
        if not isinstance(row, dict):
            continue

        description = str(row.get("description") or "").strip()
        if not description:
            continue

        if _is_non_transactional(description):
            filtered_count += 1
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

        tipo_custo_raw = str(row.get("tipo_custo") or "").strip().lower()
        tipo_custo = tipo_custo_raw if tipo_custo_raw in {"fixo", "variavel", "rendimento", "credito"} else "variavel"
        categoria_sugerida = str(row.get("categoria_sugerida") or "Outros").strip() or "Outros"
        subcategoria_sugerida = str(row.get("subcategoria_sugerida") or "").strip() or None

        normalized_rows.append(
            {
                "date": _normalize_date(row.get("date")),
                "description": description,
                "credit": abs(credit) if credit is not None else None,
                "debit": abs(debit) if debit is not None else None,
                "balance": balance,
                "raw_amount_text": raw_amount_text or None,
                "confidence": confidence if confidence in {"high", "medium", "low"} else "medium",
                "tipo_custo": tipo_custo,
                "categoria_sugerida": categoria_sugerida,
                "subcategoria_sugerida": subcategoria_sugerida,
                "confirmado": False,
            }
        )

    if filtered_count:
        logger.info("doc_filtered_non_transactions count=%d", filtered_count)

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

    return credit_entries[:200], debit_entries[:200]


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


_MAX_PDF_PROMPT_CHARS = 60_000
_MAX_SCANNED_PAGES_PER_CALL = 8


def _score_page_financial_density(text: str) -> int:
    """Score a page by how many financial data points it contains."""
    return (
        len(re.findall(r"\d{2}/\d{2}/\d{4}", text)) * 2
        + len(re.findall(r"R\$\s*\d", text)) * 2
        + len(re.findall(r"\d+[.,]\d{2}", text))
    )


_DATE_RE = re.compile(r'\b\d{2}/\d{2}/\d{2,4}\b')
_AMOUNT_RE = re.compile(r'(?:R\$\s*)?\d{1,3}(?:[.\s]\d{3})*[,]\d{2}|\b\d+[,]\d{2}\b')

_COST_TYPE_DISPLAY: dict[str, str] = {
    "fixo": "fixo",
    "variavel": "variável",
    "rendimento": "rendimento",
    "credito": "crédito",
}


def _format_cost_type(tipo_custo: str) -> str:
    """Return a user-facing, accented label for a tipo_custo internal value."""
    return _COST_TYPE_DISPLAY.get((tipo_custo or "").lower().strip(), tipo_custo)


def _count_raw_transaction_candidates(text: str) -> int:
    """Count lines that have both a date (dd/mm/yy|yyyy) and a BRL monetary value.

    Requiring both date and amount on the same line avoids counting header rows,
    balance/summary lines, and broken pdfplumber wraps that contain only dates.
    """
    count = 0
    for line in text.splitlines():
        if _DATE_RE.search(line) and _AMOUNT_RE.search(line):
            count += 1
    return count


def _anonymize_document_text(text: str) -> str:
    """Mask PII before sending text to GPT: CPF, card number, agency/account, holder name."""
    # CPF: 000.000.000-00
    text = re.sub(r'\b\d{3}\.\d{3}\.\d{3}-\d{2}\b', '[CPF]', text)

    # Full card number (16 digits in 4 groups)
    text = re.sub(r'\b\d{4}[\s\-]\d{4}[\s\-]\d{4}[\s\-]\d{4}\b', '[CARTAO]', text)
    # Masked card: xxxx-xxxx-xxxx-1234 style
    text = re.sub(r'(?:[xX*]{4}[\s\-]){3}\d{4}', '[CARTAO]', text)

    # Agency: "Agência 0001-3", "Ag: 0001", "AG 0001"
    text = re.sub(
        r'(?:Ag[eê]ncia|Agencia|Ag\.?)\s*[:.]?\s*\d{3,6}(?:[-/]\d{1,2})?',
        '[AGENCIA/CONTA]',
        text,
        flags=re.IGNORECASE,
    )
    # Account: "Conta Corrente: 12345-6", "CC: 12345-6", "C/C 123456"
    text = re.sub(
        r'(?:Conta\s*(?:Corrente)?|CC\.?|C/?C\.?)\s*[:.]?\s*\d{4,12}(?:[-/]\d{1,2})?',
        '[AGENCIA/CONTA]',
        text,
        flags=re.IGNORECASE,
    )

    # Holder name: all-caps line (2+ words) within the first 10 lines of the document
    lines = text.split('\n')
    for i, line in enumerate(lines[:10]):
        stripped = line.strip()
        if (
            stripped
            and stripped == stripped.upper()
            and len(stripped.split()) >= 2
            and len(stripped) >= 6
            and not re.search(r'\d', stripped)
        ):
            lines[i] = '[TITULAR]'
    return '\n'.join(lines)


def _ocr_image_bytes(image_bytes: bytes) -> str:
    """Extract text from image bytes using pytesseract (local OCR — no data sent externally)."""
    try:
        import pytesseract  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        image = Image.open(BytesIO(image_bytes))
        text = pytesseract.image_to_string(image, lang="por+eng")
        return re.sub(r'\s+\n', '\n', text).strip()
    except ImportError:
        logger.warning("ocr_unavailable pytesseract or Pillow not installed")
        return ""
    except Exception as exc:
        logger.warning("ocr_failed: %s", exc)
        return ""


def _extract_pdfplumber_page(page: Any) -> str:
    """
    Extract text from a pdfplumber page preserving table row structure.

    Tries line-based then text-based table detection so that multi-column
    transaction tables (Date | Description | Debit | Credit | Balance) are
    returned as pipe-separated rows instead of spatially-scrambled text.
    Falls back to plain extract_text() when no usable table is detected.
    """
    for table_settings in (
        {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
        {"vertical_strategy": "text", "horizontal_strategy": "text"},
    ):
        try:
            tables = page.extract_tables(table_settings)
        except Exception:
            tables = []
        if not tables:
            continue
        rows: list[str] = []
        for table in tables:
            for row in table:
                if not row:
                    continue
                cells = [str(cell or "").strip().replace("\n", " ") for cell in row]
                if sum(1 for c in cells if c) >= 2:
                    rows.append(" | ".join(cells))
        if len(rows) >= 2:
            return "\n".join(rows)

    text = page.extract_text() or ""
    return re.sub(r"\s+\n", "\n", text).strip()


def _get_pdf_pages_text(media_bytes: bytes) -> tuple[list[tuple[int, str]], int]:
    """
    Extract text from every page, returning ([(page_index, text), ...], total_pages).

    Prefers pdfplumber for its superior table-aware extraction; falls back to
    pypdf when pdfplumber is not installed.
    """
    try:
        import pdfplumber  # noqa: PLC0415

        indexed: list[tuple[int, str]] = []
        with pdfplumber.open(BytesIO(media_bytes)) as pdf:
            total_pages = len(pdf.pages)
            for i, page in enumerate(pdf.pages):
                text = _extract_pdfplumber_page(page)
                if text:
                    indexed.append((i, text))
        logger.debug(
            "pdf_pages_extracted total=%d extracted=%d method=pdfplumber",
            total_pages,
            len(indexed),
        )
        return indexed, total_pages
    except ImportError:
        pass

    reader = PdfReader(BytesIO(media_bytes))
    total_pages = len(reader.pages)
    indexed = []
    for i, page in enumerate(reader.pages):
        text = re.sub(r"\s+\n", "\n", page.extract_text() or "").strip()
        if text:
            indexed.append((i, text))
    logger.debug(
        "pdf_pages_extracted total=%d extracted=%d method=pypdf",
        total_pages,
        len(indexed),
    )
    return indexed, total_pages


def _extract_pdf_text(media_bytes: bytes) -> str:
    indexed, _ = _get_pdf_pages_text(media_bytes)
    return "\n\n".join(text for _, text in indexed)


def _get_pdf_text_for_prompt(media_bytes: bytes) -> tuple[str, int, int]:
    """
    Returns (text_for_prompt, pages_included, total_pages).

    When the full text fits within _MAX_PDF_PROMPT_CHARS all pages are returned.
    For larger documents a smart selection is applied:
      - first page (header, account info) and last page (totals/summary) are always included;
      - remaining budget is filled with middle pages ranked by financial data density.
    """
    indexed, total_pages = _get_pdf_pages_text(media_bytes)

    if not indexed:
        return "", 0, total_pages

    full_text = "\n\n".join(t for _, t in indexed)
    if len(full_text) <= _MAX_PDF_PROMPT_CHARS:
        return full_text, total_pages, total_pages

    # Smart selection: anchor on first + last page, then fill by density
    anchor_indices: set[int] = {indexed[0][0]}
    selected: list[tuple[int, str]] = [indexed[0]]
    if len(indexed) > 1:
        anchor_indices.add(indexed[-1][0])
        selected.append(indexed[-1])

    middle = sorted(
        [(i, t) for i, t in indexed if i not in anchor_indices],
        key=lambda x: _score_page_financial_density(x[1]),
        reverse=True,
    )

    char_budget = _MAX_PDF_PROMPT_CHARS - sum(len(t) + 2 for _, t in selected)
    for i, text in middle:
        if len(text) + 2 <= char_budget:
            selected.append((i, text))
            char_budget -= len(text) + 2

    selected.sort(key=lambda x: x[0])
    return "\n\n".join(t for _, t in selected), len(selected), total_pages


def _get_categories_for_prompt() -> str:
    try:
        from app.db import get_cursor
        with get_cursor() as (_, cursor):
            cursor.execute("SELECT nome, subcategorias FROM categorias ORDER BY nome")
            rows = cursor.fetchall()
        if not rows:
            return ""
        lines = ["Categorias disponíveis para classificação (use exatamente esses nomes):"]
        for nome, subcats in rows:
            if subcats:
                lines.append(f"- {nome}: {subcats}")
            else:
                lines.append(f"- {nome}")
        return "\n".join(lines)
    except Exception:
        return ""


def _build_document_analysis_system_prompt(income_instructions: str, hinted_type: str, user_locale: str = "pt-BR") -> str:
    categories_hint = _get_categories_for_prompt()
    base = (
        f"Idioma do usuário: {user_locale}. "
        "Retorne TODAS as strings (categorias, subcategorias, descrições resumidas) com ortografia e acentuação CORRETAS desse idioma. "
        "Voce analisa documentos financeiros e retorna TODOS os lancamentos encontrados, sem excecao. "
        "Nao filtre por tipo, valor ou categoria — creditos, debitos, valores minimos, lancamentos sem categoria clara — TODOS devem ser retornados em statement_rows. "
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
        '"account_limit":numero ou null,'
        '"pending_charges":numero ou null,'
        '"charges_debit_date":"YYYY-MM-DD" ou null,'
        '"statement_rows":[{"date":"YYYY-MM-DD ou texto","description":"texto EXATAMENTE como no documento","credit":numero ou null,"debit":numero ou null,"balance":numero ou null,"raw_amount_text":"texto ou null","confidence":"high|medium|low","tipo_custo":"fixo|variavel|rendimento|credito","categoria_sugerida":"nome da categoria com acentuação correta no idioma do usuário ou Outros","subcategoria_sugerida":"subcategoria especifica com acentuação correta no idioma do usuário ou null","confirmado":false}] — INCLUA ABSOLUTAMENTE TODOS os lancamentos sem filtrar,'
        '"credit_entries":[{"date":"YYYY-MM-DD ou texto","description":"texto","amount":numero}] (TODOS os creditos: remuneracao de aplicacao, rendimento, estorno, transferencia — mesmo R$0,01),'
        '"debit_entries":[{"date":"YYYY-MM-DD ou texto","description":"texto","amount":numero}] (TODOS os debitos: cartao de debito, PIX enviado, tarifa, compra — mesmo valores pequenos),'
        '"estimated_fixed_expenses":numero ou null,'
        '"top_items":["item 1","item 2"]}. '
        "Para faturas de cartao: credit_limit e o limite total do cartao (campo 'limite', 'limite do cartao' ou similar). "
        "best_purchase_day e o melhor dia para compras (campo 'melhor dia para compras', 'data de fechamento' menos alguns dias, ou similar). "
        "Se nao estiver explicito, retorne null. "
        f"{income_instructions}"
    )
    if categories_hint:
        base += f"\n\n{categories_hint}"
    return base


def _render_pdf_pages_as_images(media_bytes: bytes) -> list[bytes]:
    """Renders all PDF pages as PNG images using pymupdf (for scanned/image-based PDFs)."""
    try:
        import fitz  # noqa: PLC0415 — pymupdf

        doc = fitz.open(stream=media_bytes, filetype="pdf")
        return [doc.load_page(i).get_pixmap(dpi=150).tobytes("png") for i in range(len(doc))]
    except Exception:
        return []


def _call_openai_vision_with_pages(
    client: "OpenAI",
    system_content: str,
    user_text: str,
    page_images: list[bytes],
) -> dict[str, Any]:
    """Single GPT-4.1-mini vision call with one or more page images."""
    image_content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for img_bytes in page_images:
        encoded = base64.b64encode(img_bytes).decode("utf-8")
        image_content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}
        )
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": image_content},
        ],
    )
    return _parse_openai_json(response.choices[0].message.content or "{}")


def _select_scanned_pages(page_images: list[bytes]) -> list[bytes]:
    """
    For PDFs with more pages than _MAX_SCANNED_PAGES_PER_CALL, select the most
    representative subset: first half (header/transactions) + last pages (totals/summary).
    """
    half = _MAX_SCANNED_PAGES_PER_CALL // 2
    return page_images[:half] + page_images[-half:]


def _extract_scanned_pdf_with_openai(
    message_text: str,
    media_bytes: bytes,
    hinted_type: str,
    system_content: str,
) -> dict[str, Any] | None:
    """
    Sends rendered PDF pages to GPT-4.1-mini vision when pypdf extracts no text.
    Uses batched/selected pages for PDFs exceeding _MAX_SCANNED_PAGES_PER_CALL.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        return None

    all_pages = _render_pdf_pages_as_images(media_bytes)
    if not all_pages:
        return None

    total_pages = len(all_pages)
    selected_pages = all_pages if total_pages <= _MAX_SCANNED_PAGES_PER_CALL else _select_scanned_pages(all_pages)
    pages_included = len(selected_pages)

    client = OpenAI(api_key=settings.openai_api_key)
    user_text = (
        f"Mensagem do usuario: {message_text or 'sem legenda'}.\n"
        f"Tipo sugerido inicialmente: {hinted_type}.\n"
        "As imagens abaixo sao paginas de um PDF escaneado. "
        "Extraia todas as linhas visiveis com alta precisao e preencha statement_rows linha a linha sem omitir entradas."
    )

    analysis = _call_openai_vision_with_pages(client, system_content, user_text, selected_pages)
    analysis["document_type"] = analysis.get("document_type") or hinted_type
    analysis["_pages_included"] = pages_included
    analysis["_total_pages"] = total_pages
    analysis["statement_rows"] = _normalize_statement_rows(analysis.get("statement_rows"))
    derived_credit, derived_debit = _derive_statement_entries(analysis["statement_rows"])
    analysis["credit_entries"] = derived_credit or _normalize_statement_entries(analysis.get("credit_entries"))
    analysis["debit_entries"] = derived_debit or _normalize_statement_entries(analysis.get("debit_entries"))
    return _normalize_income_signal(analysis, hinted_type)


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
                "Se quiser incluir uma fatura de cartão agora, pode me mandar por aqui.\n\n"
                'Pode ser imagem ou PDF. Se preferir deixar isso para depois, é só responder "PULAR".'
            )
        return (
            f"Perfeito. Como eu já tenho seu extrato, agora pode me mandar {invoice_target}.\n\n"
            'Pode ser imagem ou PDF. Se mudar de ideia, é só responder "PULAR".'
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
                "Recebi sua mídia, mas por enquanto consigo trabalhar apenas com imagem ou PDF. "
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
    user_locale: str = "pt-BR",
) -> dict[str, Any] | None:
    settings = get_settings()
    if media_content_type not in SUPPORTED_MEDIA_TYPES:
        return None
    if not settings.openai_api_key:
        return None

    income_instructions = _income_detection_instructions(hinted_type)
    system_content = _build_document_analysis_system_prompt(income_instructions, hinted_type, user_locale)

    if media_content_type == "application/pdf":
        # ETAPA 1: Extract ALL pages locally — no page limit, fresh extraction every time
        indexed, total_pages = _get_pdf_pages_text(media_bytes)
        extracted_text = "\n\n".join(text for _, text in indexed)
        pages_included = total_pages

        if not extracted_text:
            # Scanned PDF: render with pymupdf then OCR each page locally
            page_images = _render_pdf_pages_as_images(media_bytes)
            total_pages = len(page_images)
            pages_included = total_pages
            ocr_parts: list[str] = []
            for img_bytes in page_images:
                ocr_text = _ocr_image_bytes(img_bytes)
                if ocr_text:
                    ocr_parts.append(ocr_text)
            extracted_text = "\n\n".join(ocr_parts)

        if not extracted_text:
            logger.warning("doc_no_text_extracted media_type=%s hinted_type=%s", media_content_type, hinted_type)
            return None

        raw_count = _count_raw_transaction_candidates(extracted_text)
        logger.info("doc_extraction_local pages=%d raw_candidates=%d hinted_type=%s", pages_included, raw_count, hinted_type)

        # ETAPA 2: Anonymize before sending to GPT
        anonymized_text = _anonymize_document_text(extracted_text)

        # ETAPA 3: GPT classifies entries only — never receives raw PDF or image
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": (
                        f"Mensagem do usuario: {message_text or 'sem legenda'}.\n"
                        f"Tipo sugerido inicialmente: {hinted_type}.\n"
                        f"Paginas incluidas: {pages_included} de {total_pages}. "
                        "Texto extraido localmente e anonimizado. "
                        "Classifique TODOS os lancamentos presentes sem omitir nenhum, "
                        "independente do valor ou tipo. Preencha statement_rows linha a linha.\n\n"
                        f"{anonymized_text}"
                    ),
                },
            ],
        )
    else:
        # Image: OCR locally first — never send original image to GPT
        extracted_text = _ocr_image_bytes(media_bytes)
        pages_included = 1
        total_pages = 1

        if not extracted_text:
            logger.warning(
                "doc_ocr_failed media_type=%s hinted_type=%s — image not sent to GPT per privacy policy",
                media_content_type,
                hinted_type,
            )
            return None

        raw_count = _count_raw_transaction_candidates(extracted_text)
        logger.info("doc_ocr_local raw_candidates=%d hinted_type=%s", raw_count, hinted_type)

        # ETAPA 2: Anonymize
        anonymized_text = _anonymize_document_text(extracted_text)

        # ETAPA 3: GPT receives only anonymized text
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": (
                        f"Mensagem do usuario: {message_text or 'sem legenda'}.\n"
                        f"Tipo sugerido inicialmente: {hinted_type}.\n"
                        "Texto extraido via OCR local e anonimizado. "
                        "Classifique TODOS os lancamentos presentes sem omitir nenhum.\n\n"
                        f"{anonymized_text}"
                    ),
                },
            ],
        )

    content = response.choices[0].message.content or "{}"
    analysis = _parse_openai_json(content)
    analysis["document_type"] = analysis.get("document_type") or hinted_type
    analysis["_pages_included"] = pages_included
    analysis["_total_pages"] = total_pages

    analysis["statement_rows"] = _normalize_statement_rows(analysis.get("statement_rows"))
    derived_credit_entries, derived_debit_entries = _derive_statement_entries(analysis["statement_rows"])
    analysis["credit_entries"] = derived_credit_entries or _normalize_statement_entries(analysis.get("credit_entries"))
    analysis["debit_entries"] = derived_debit_entries or _normalize_statement_entries(analysis.get("debit_entries"))

    # ETAPA 5: Completeness verification — retry once if GPT returned fewer entries than expected
    gpt_count = len(analysis["statement_rows"])
    analysis["_raw_candidates"] = raw_count
    if raw_count > 0 and gpt_count < raw_count:
        logger.warning(
            "doc_completeness_mismatch raw_candidates=%d gpt_classified=%d hinted_type=%s — retrying",
            raw_count,
            gpt_count,
            hinted_type,
        )
        try:
            retry_response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": system_content},
                    {
                        "role": "user",
                        "content": (
                            f"ATENCAO: na tentativa anterior retornei {gpt_count} lancamentos, "
                            f"mas o texto contem aproximadamente {raw_count} linhas com data. "
                            "Retorne TODOS os lancamentos — nao descarte nenhum, "
                            "mesmo creditos de rendimento, valores pequenos ou lancamentos sem categoria clara. "
                            "Cada linha com data no texto deve gerar uma entrada em statement_rows.\n\n"
                            f"{anonymized_text}"
                        ),
                    },
                ],
            )
            retry_content = retry_response.choices[0].message.content or "{}"
            retry_analysis = _parse_openai_json(retry_content)
            retry_rows = _normalize_statement_rows(retry_analysis.get("statement_rows"))
            if len(retry_rows) > gpt_count:
                logger.info(
                    "doc_retry_improved gpt_before=%d gpt_after=%d",
                    gpt_count,
                    len(retry_rows),
                )
                analysis["statement_rows"] = retry_rows
                derived_credit_entries, derived_debit_entries = _derive_statement_entries(retry_rows)
                analysis["credit_entries"] = derived_credit_entries or _normalize_statement_entries(retry_analysis.get("credit_entries"))
                analysis["debit_entries"] = derived_debit_entries or _normalize_statement_entries(retry_analysis.get("debit_entries"))
                for key in ("current_balance", "account_limit", "pending_charges", "charges_debit_date",
                            "detected_income", "income_description", "income_confidence", "summary"):
                    if retry_analysis.get(key) is not None and analysis.get(key) is None:
                        analysis[key] = retry_analysis[key]
        except Exception as exc:
            logger.warning("doc_retry_failed: %s", exc)

    return _normalize_income_signal(analysis, hinted_type)


def _update_document_analysis(document_id: int, analysis: dict[str, Any] | None) -> None:
    payload = json.dumps(analysis, ensure_ascii=False) if analysis else None
    status = "processado" if analysis else "falha"
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
    account_limit = _safe_float(analysis.get("account_limit"))
    pending_charges = _safe_float(analysis.get("pending_charges"))
    charges_debit_date = _normalize_date(analysis.get("charges_debit_date"))

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
                limite_conta,
                provisao_encargos,
                data_debito_encargos,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET
                saldo_atual_estimado = COALESCE(EXCLUDED.saldo_atual_estimado, perfil_financeiro.saldo_atual_estimado),
                renda_identificada = COALESCE(EXCLUDED.renda_identificada, perfil_financeiro.renda_identificada),
                despesas_fixas_estimadas = COALESCE(EXCLUDED.despesas_fixas_estimadas, perfil_financeiro.despesas_fixas_estimadas),
                pressao_cartao = COALESCE(EXCLUDED.pressao_cartao, perfil_financeiro.pressao_cartao),
                limite_conta = COALESCE(EXCLUDED.limite_conta, perfil_financeiro.limite_conta),
                provisao_encargos = COALESCE(EXCLUDED.provisao_encargos, perfil_financeiro.provisao_encargos),
                data_debito_encargos = COALESCE(EXCLUDED.data_debito_encargos, perfil_financeiro.data_debito_encargos),
                updated_at = NOW()
            """,
            (user_id, current_balance, detected_income, estimated_fixed_expenses, pressure,
             account_limit, pending_charges, charges_debit_date),
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


def _render_statement_rows(rows: list[dict[str, Any]]) -> list[str]:
    """Render statement_rows as display lines, including header and call-to-action.

    Returns an empty list when rows is empty so callers can extend unconditionally.
    Prepends a blank separator line when non-empty.
    """
    if not rows:
        return []
    result: list[str] = ["", f"Lançamentos encontrados ({len(rows)} no total):"]
    for row in rows[:20]:
        date_prefix = f"{row['date']} " if row.get("date") else ""
        if row.get("credit") is not None:
            valor_str = f"+R${row['credit']:.2f}"
        elif row.get("debit") is not None:
            valor_str = f"-R${row['debit']:.2f}"
        else:
            valor_str = ""
        tipo = _format_cost_type(row.get("tipo_custo") or "")
        cat = row.get("categoria_sugerida") or ""
        subcat = row.get("subcategoria_sugerida") or ""
        cat_display = f"{cat}/{subcat}" if cat and subcat else cat
        meta = " | ".join(part for part in [tipo, cat_display] if part)
        suffix = f" ({meta})" if meta else ""
        val_part = f": {valor_str}" if valor_str else ""
        result.append(f"- {date_prefix}{row['description']}{val_part}{suffix}")
    if len(rows) > 20:
        result.append(f"- ...e mais {len(rows) - 20} lançamentos.")
    result.extend([
        "",
        'Me responda "SIM" para confirmar esses lançamentos, ou me diga o que precisa ajustar.',
    ])
    return result


def _build_adjustments_applied_message(
    updated_rows: list[dict[str, Any]],
    original_rows: list[dict[str, Any]],
) -> str:
    """Return a lean confirmation after apply_user_adjustments(), listing only what changed."""
    ignored_count = len(original_rows) - len(updated_rows)
    original_by_desc = {r.get("description", ""): r for r in original_rows}
    changes: list[str] = []

    for row in updated_rows:
        desc = row.get("description", "")
        orig = original_by_desc.get(desc)
        if orig is None:
            continue
        parts: list[str] = []
        if orig.get("categoria_sugerida") != row.get("categoria_sugerida"):
            parts.append(f"categoria: {row.get('categoria_sugerida') or 'Outros'}")
        if orig.get("subcategoria_sugerida") != row.get("subcategoria_sugerida"):
            new_sub = row.get("subcategoria_sugerida") or "sem subcategoria"
            parts.append(f"subcategoria: {new_sub}")
        if orig.get("tipo_custo") != row.get("tipo_custo"):
            parts.append(f"tipo: {_format_cost_type(row.get('tipo_custo', ''))}")
        if parts:
            changes.append(f"- {desc[:50]}: {', '.join(parts)}")

    if ignored_count > 0:
        changes.append(f"- {ignored_count} lançamento(s) removido(s) da lista")

    if not changes:
        return 'Não identifiquei nenhum ajuste para aplicar. Me diga o que quer mudar ou confirme com "SIM".'

    return "\n".join([
        "Ajustei conforme solicitado:",
        "",
        *changes,
        "",
        'Me responda "SIM" para confirmar a lista atualizada ou diga o que mais precisa ajustar.',
    ])


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
        total_lancamentos = len(statement_rows) or (len(credit_entries) + len(debit_entries))
        if total_lancamentos > 0:
            lines.append(f"Recebi seu extrato e encontrei {total_lancamentos} lançamentos.")
        else:
            lines.append("Recebi seu extrato e consegui identificar alguns sinais importantes.")
        if current_balance is not None:
            lines.append(f"- saldo estimado: R${current_balance:.2f}")
        if detected_income is not None:
            lines.append(f"- renda identificada: R${detected_income:.2f}")
            if income_description:
                lines.append(f"- origem mais provável da renda: {income_description}")
            confidence_label = _format_income_confidence(income_confidence)
            if confidence_label:
                lines.append(f"- confiança da renda identificada: {confidence_label}")
        elif income_kind in {"investment", "transfer", "refund"} and income_description:
            labels = {
                "investment": "uma movimentação de investimento",
                "transfer": "uma transferência recebida",
                "refund": "um estorno ou reembolso",
            }
            lines.append(f"- encontrei um crédito, mas ele parece ser {labels.get(income_kind, 'um crédito pontual')} e não renda recorrente")
            lines.append(f"- origem observada: {income_description}")
        account_limit = _safe_float(analysis.get("account_limit"))
        if account_limit is not None:
            lines.append(f"- limite da conta: {format_brl(account_limit)}")
        pending_charges = _safe_float(analysis.get("pending_charges"))
        if pending_charges is not None:
            lines.append(f"- provisao de encargos (juros/IOF): {format_brl(pending_charges)}")
            charges_debit_date = analysis.get("charges_debit_date")
            if charges_debit_date:
                formatted_charges_date = format_ptbr_date(charges_debit_date) or charges_debit_date
                lines.append(f"- data prevista de debito dos encargos: {formatted_charges_date}")

        if statement_rows:
            lines.extend(_render_statement_rows(statement_rows))
        elif credit_entries or debit_entries:
            if credit_entries:
                lines.extend(["", f"Créditos identificados ({len(credit_entries)} no total):"])
                for entry in credit_entries[:10]:
                    prefix = f"{entry['date']} - " if entry.get("date") else ""
                    lines.append(f"- {prefix}{entry['description']}: R${entry['amount']:.2f}")
                if len(credit_entries) > 10:
                    lines.append(f"- ...e mais {len(credit_entries) - 10} lançamentos de crédito.")
            if debit_entries:
                lines.extend(["", f"Débitos identificados ({len(debit_entries)} no total):"])
                for entry in debit_entries[:10]:
                    prefix = f"{entry['date']} - " if entry.get("date") else ""
                    lines.append(f"- {prefix}{entry['description']}: R${entry['amount']:.2f}")
                if len(debit_entries) > 10:
                    lines.append(f"- ...e mais {len(debit_entries) - 10} lançamentos de débito.")
    elif document_type == "fatura_cartao":
        total_lancamentos = len(statement_rows)
        if total_lancamentos > 0:
            lines.append(f"Recebi sua fatura e encontrei {total_lancamentos} lançamentos.")
        else:
            lines.append("Recebi sua fatura e já extraí alguns dados importantes.")
        if invoice_total is not None:
            lines.append(f"- valor total da fatura: {format_brl(invoice_total)}")
        if due_date:
            formatted_due = format_ptbr_date(due_date) or due_date
            lines.append(f"- vencimento: {formatted_due}")
        if minimum_payment is not None:
            lines.append(f"- pagamento mínimo: {format_brl(minimum_payment)}")
        credit_limit = _safe_float(analysis.get("credit_limit"))
        if credit_limit is not None:
            lines.append(f"- limite do cartão: {format_brl(credit_limit)}")
        best_purchase_day_raw = analysis.get("best_purchase_day")
        if best_purchase_day_raw is not None:
            try:
                day = int(best_purchase_day_raw)
                if 1 <= day <= 31:
                    lines.append(f"- melhor dia para compras: dia {day}")
            except (TypeError, ValueError):
                pass
        lines.extend(_render_statement_rows(statement_rows))
    else:
        lines.append("Recebi seu documento financeiro e consegui registrar algumas informações iniciais.")

    if summary:
        lines.extend(["", summary])

    pages_included = analysis.get("_pages_included")
    total_pages = analysis.get("_total_pages")
    if pages_included is not None and total_pages is not None and pages_included < total_pages:
        lines.extend(
            [
                "",
                f"Analisei {pages_included} das {total_pages} páginas do documento, "
                "priorizando as seções com mais dados financeiros. "
                "Se quiser que eu veja o restante, pode me mandar o arquivo dividido em partes.",
            ]
        )

    lines.extend(
        [
            "",
            "Vou usar essas informações para deixar meus alertas financeiros mais inteligentes.",
        ]
    )
    msg = "\n".join(lines)
    rows_count = len(statement_rows)
    logger.info("build_analysis_message doc_type=%s rows_count=%d msg_len=%d", document_type, rows_count, len(msg))
    if rows_count > 0 and len(msg) < 500:
        logger.warning("build_analysis_message_short doc_type=%s rows_count=%d msg_len=%d — possible rendering bug", document_type, rows_count, len(msg))
    return msg


def process_received_document(user_id: int, media_url: str, media_content_type: str, message_text: str) -> str:
    hinted_type = infer_document_type(message_text, media_content_type)
    document_id = _store_document(user_id, hinted_type, media_content_type, media_url)

    if media_content_type not in SUPPORTED_MEDIA_TYPES:
        return (
            "Recebi seu documento, mas por enquanto eu só consigo analisar imagens e PDFs. "
            "Vou guardar essa referência para as próximas evoluções."
        )

    user_locale = get_user_locale(user_id)
    try:
        media_bytes = _download_media_bytes(media_url)
        analysis = _extract_document_analysis(message_text, media_content_type, media_bytes, hinted_type, user_locale)
    except Exception:
        analysis = None

    _update_document_analysis(document_id, analysis)

    if not analysis:
        return build_document_receipt_message(hinted_type)

    _persist_financial_context(user_id, analysis)
    return _build_analysis_message(analysis, hinted_type)


_ADJUSTMENTS_ERROR_MSG = (
    "Não consegui entender seus ajustes. "
    "Você pode me enviar novamente de forma mais estruturada? "
    'Por exemplo: "lançamento 3: categoria Alimentação, subcategoria Mercado, tipo variável"'
)

_ADJUSTMENTS_EXAMPLE = (
    'Exemplo de saida esperada: [{"indice":2,"acao":"atualizar","categoria":"Alimentacao","subcategoria":"supermercado","tipo_custo":"variavel"},'
    '{"indice":5,"acao":"ignorar","categoria":null,"subcategoria":null,"tipo_custo":null}]'
)


def apply_user_adjustments(
    raw_text: str,
    rows: list[dict[str, Any]],
    user_id: int | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Parse natural-language adjustments and apply them to statement rows.

    Returns (updated_rows, error_message). error_message is None on success.
    """
    settings = get_settings()
    if not settings.openai_api_key or not rows:
        return rows, None

    rows_summary = "\n".join(
        f'{i}: {r.get("date", "")} {r["description"]} '
        f'({r.get("tipo_custo", "")}/{r.get("categoria_sugerida", "")}'
        f'{"/" + r["subcategoria_sugerida"] if r.get("subcategoria_sugerida") else ""})'
        for i, r in enumerate(rows)
    )
    user_locale = get_user_locale(user_id) if user_id is not None else "pt-BR"
    client = OpenAI(api_key=settings.openai_api_key)
    gpt_content: str = "[]"
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Idioma do usuário: {user_locale}. "
                        "Retorne todas as strings (categoria, subcategoria) com ortografia e acentuação CORRETAS desse idioma. "
                        "Voce recebe uma lista numerada de lancamentos financeiros e uma mensagem do usuario com ajustes. "
                        "Retorne APENAS um array JSON valido, sem markdown, sem texto explicativo, sem objeto envolvente. "
                        "Formato exato: "
                        '[{"indice":N,"acao":"atualizar"|"ignorar",'
                        '"categoria":"nova categoria ou null","subcategoria":"nova subcategoria ou null",'
                        '"tipo_custo":"fixo|variavel|rendimento|credito ou null"}]. '
                        "Use 'ignorar' para descartar o lancamento da lista. "
                        "Identifique o lancamento pelo numero (indice) ou por palavras da descricao. "
                        "Se a mensagem nao contiver ajustes reconheciveis, retorne []. "
                        f"{_ADJUSTMENTS_EXAMPLE}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Lancamentos atuais:\n{rows_summary}\n\n"
                        f"Ajuste solicitado: {raw_text}"
                    ),
                },
            ],
        )
        gpt_content = response.choices[0].message.content or "[]"
        adjustments = _parse_openai_json_list(gpt_content)
    except Exception:
        logger.exception(
            "adjustments_failed raw_gpt_response=%.300s user_text=%.200s user_id=%s",
            gpt_content, raw_text, user_id,
        )
        return rows, _ADJUSTMENTS_ERROR_MSG

    if not adjustments:
        logger.info("adjustments_applied count=0 user_id=%s", user_id)
        return rows, None

    updated = [dict(r) for r in rows]
    ignored_indices: set[int] = set()
    applied = 0
    failed = 0

    for adj in adjustments:
        idx = adj.get("indice")
        if not isinstance(idx, int) or idx < 0 or idx >= len(updated):
            failed += 1
            continue
        if adj.get("acao") == "ignorar":
            ignored_indices.add(idx)
            applied += 1
            continue
        if adj.get("categoria"):
            updated[idx]["categoria_sugerida"] = str(adj["categoria"]).strip()
        if "subcategoria" in adj and adj["subcategoria"] is not None:
            updated[idx]["subcategoria_sugerida"] = str(adj["subcategoria"]).strip() or None
        if adj.get("tipo_custo") and adj["tipo_custo"] in {"fixo", "variavel", "rendimento", "credito"}:
            updated[idx]["tipo_custo"] = adj["tipo_custo"]
        applied += 1

    result = [r for i, r in enumerate(updated) if i not in ignored_indices]

    if failed:
        logger.warning("adjustments_partial applied=%d failed=%d user_id=%s", applied, failed, user_id)
    else:
        logger.info("adjustments_applied count=%d user_id=%s", applied, user_id)

    return result, None


def save_extrato_adjustments(user_id: int, updated_rows: list[dict[str, Any]]) -> None:
    """Persist adjusted statement_rows back to the latest extrato extracted_json."""
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT id, extracted_json FROM documentos_financeiros
            WHERE user_id = %s AND tipo_documento = 'extrato'
              AND extracted_json IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
    if not row:
        return
    doc_id, extracted_json_str = row
    try:
        analysis = json.loads(extracted_json_str)
    except Exception:
        return
    analysis["statement_rows"] = updated_rows
    updated_json = json.dumps(analysis, ensure_ascii=False)
    with get_cursor() as (conn, cursor):
        cursor.execute(
            "UPDATE documentos_financeiros SET extracted_json = %s WHERE id = %s",
            (updated_json, doc_id),
        )
        conn.commit()


def mark_extrato_reviewed(user_id: int) -> None:
    """Mark the latest extrato document as reviewed by the user."""
    with get_cursor() as (conn, cursor):
        cursor.execute(
            """
            UPDATE documentos_financeiros
            SET revisado = TRUE
            WHERE id = (
                SELECT id FROM documentos_financeiros
                WHERE user_id = %s AND tipo_documento = 'extrato'
                ORDER BY created_at DESC
                LIMIT 1
            )
            """,
            (user_id,),
        )
        conn.commit()


def is_document_processing(user_id: int) -> bool:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT 1 FROM documentos_financeiros
            WHERE user_id = %s AND status_processamento = 'recebido'
            LIMIT 1
            """,
            (user_id,),
        )
        return cursor.fetchone() is not None


def get_latest_extrato_analysis(user_id: int) -> dict[str, Any] | None:
    with get_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT extracted_json FROM documentos_financeiros
            WHERE user_id = %s AND tipo_documento = 'extrato'
              AND extracted_json IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def process_stored_document(
    document_id: int,
    user_id: int,
    media_url: str,
    media_content_type: str,
    message_text: str,
    hinted_type: str,
    card_id: int | None = None,
    on_complete_numero: str | None = None,
    on_complete_state: str | None = None,
) -> None:
    logger.info("doc_processing_start document_id=%d user_id=%d type=%s", document_id, user_id, hinted_type)
    user_locale = get_user_locale(user_id)
    try:
        media_bytes = _download_media_bytes(media_url)
        analysis = _extract_document_analysis(message_text, media_content_type, media_bytes, hinted_type, user_locale)
    except Exception:
        logger.exception("doc_processing_error document_id=%d user_id=%d", document_id, user_id)
        analysis = None

    _update_document_analysis(document_id, analysis)

    if analysis:
        _persist_financial_context(user_id, analysis, card_id)
        logger.info("doc_processing_done document_id=%d user_id=%d", document_id, user_id)
        if on_complete_numero:
            from app.services.onboarding import set_onboarding_state
            from app.services.twilio import responder
            import time as _time
            if on_complete_state:
                set_onboarding_state(user_id, on_complete_state)
            proactive_msg = _build_analysis_message(analysis, hinted_type)
            logger.info(
                "doc_proactive_attempt document_id=%d user_id=%d numero=%s msg_len=%d",
                document_id, user_id, on_complete_numero, len(proactive_msg),
            )
            _sent = False
            for _attempt, _delay in enumerate([0, 1, 2], 1):
                if _delay:
                    _time.sleep(_delay)
                try:
                    responder(on_complete_numero, proactive_msg)
                    logger.info(
                        "doc_proactive_ok document_id=%d user_id=%d attempt=%d",
                        document_id, user_id, _attempt,
                    )
                    _sent = True
                    break
                except Exception:
                    logger.exception(
                        "doc_proactive_error document_id=%d user_id=%d attempt=%d",
                        document_id, user_id, _attempt,
                    )
            if not _sent:
                logger.error(
                    "doc_proactive_all_attempts_failed document_id=%d user_id=%d",
                    document_id, user_id,
                )
    else:
        logger.warning("doc_processing_no_analysis document_id=%d user_id=%d", document_id, user_id)


def build_document_receipt_message(tipo_documento: str, partial: bool = False) -> str:
    if tipo_documento == "fatura_cartao":
        detalhe = "Recebi sua fatura e já deixei salva por aqui."
    elif tipo_documento == "extrato":
        detalhe = "Recebi seu extrato e já deixei salvo por aqui."
    else:
        detalhe = "Recebi seu documento e já deixei salvo por aqui."

    if partial:
        return (
            f"{detalhe}\n\n"
            "Consegui processar parte do conteúdo, mas o arquivo parece ser escaneado ou estar "
            "em um formato que dificultou a leitura completa. "
            "Se quiser, pode me mandar uma versão em texto ou dividida em partes menores — "
            "assim consigo extrair todos os dados com mais precisão."
        )

    return (
        f"{detalhe}\n\n"
        "Estou analisando o conteúdo em segundo plano e vou usar essas informações "
        "para deixar o seu acompanhamento financeiro mais preciso."
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


def build_onboarding_completion_message(user_id: int) -> tuple[str, str]:
    """Returns (summary_message, ready_message) to be sent as two separate messages."""
    from app.services.onboarding import get_user_name  # noqa: PLC0415

    nome = get_user_name(user_id)

    with get_cursor() as (_, cursor):
        cursor.execute(
            "SELECT saldo_atual_estimado FROM perfil_financeiro WHERE user_id = %s",
            (user_id,),
        )
        row = cursor.fetchone()
    saldo = float(row[0]) if row and row[0] is not None else None

    cards = get_cards(user_id)
    invoices: list[dict[str, Any]] = []
    if cards:
        with get_cursor() as (_, cursor):
            for card in cards:
                cursor.execute(
                    """
                    SELECT valor_total, vencimento
                    FROM faturas_cartao
                    WHERE user_id = %s AND cartao_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (user_id, card["id"]),
                )
                inv = cursor.fetchone()
                if inv:
                    invoices.append(
                        {
                            "nome": str(card["nome_cartao"]),
                            "valor": float(inv[0]),
                            "vencimento": inv[1],
                        }
                    )

    linhas: list[str] = ["Aqui está um resumo do que organizei para você:"]
    if saldo is not None:
        linhas.append(f"\nSaldo atual da conta: {format_brl(saldo)}")
    if invoices:
        linhas.append("\nFaturas dos cartões:")
        for inv in invoices:
            venc = format_ptbr_date(inv["vencimento"]) or ""
            linha = f"- {inv['nome']}: {format_brl(inv['valor'])}"
            if venc:
                linha += f" (venc. {venc})"
            linhas.append(linha)

    summary_message = "\n".join(linhas)

    saudacao = f"Pronto, {nome}!" if nome else "Pronto!"
    ready_message = (
        f"{saudacao} Sua base financeira está organizada e agora posso te acompanhar de um jeito muito mais completo.\n\n"
        "Pode interagir comigo pelo WhatsApp usando:\n"
        "- Texto: registre gastos, receitas ou perguntas\n"
        "- Voz: mande um áudio com o que quiser registrar\n"
        "- Imagem: foto de comprovante, extrato ou fatura\n"
        "- Arquivo PDF: extrato bancário ou fatura do cartão\n\n"
        "Sempre que precisar, pode me chamar por aqui."
    )

    return summary_message, ready_message
