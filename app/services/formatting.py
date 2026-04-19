import re
from datetime import datetime
from typing import Any

# Maps unaccented PT-BR words (lower-case) to their correct accented forms.
# Keys must be whole words; matching is case-insensitive and preserves original capitalisation.
_PTBR_ACCENT_MAP: dict[str, str] = {
    "alimentacao": "alimentação",
    "educacao": "educação",
    "comunicacao": "comunicação",
    "habitacao": "habitação",
    "manutencao": "manutenção",
    "variavel": "variável",
    "variaveis": "variáveis",
    "automovel": "automóvel",
    "automoveis": "automóveis",
    "condominio": "condomínio",
    "saude": "saúde",
    "agua": "água",
    "combustivel": "combustível",
    "farmacia": "farmácia",
    "medico": "médico",
    "medica": "médica",
    "emergencia": "emergência",
    "assinatura": "assinatura",
    "pedagio": "pedágio",
    "eletricidade": "eletricidade",
    "telefonia": "telefonia",
    "academica": "acadêmica",
    "estacionamento": "estacionamento",
}

_PTBR_ACCENT_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _PTBR_ACCENT_MAP) + r")\b",
    re.IGNORECASE,
)


def _restore_case(original: str, corrected: str) -> str:
    """Apply the capitalisation pattern of *original* to *corrected*."""
    if original.isupper():
        return corrected.upper()
    if original[0].isupper():
        return corrected[0].upper() + corrected[1:]
    return corrected


def normalize_ptbr_accents(text: str) -> str:
    """Fix common missing PT-BR accents in a string, preserving capitalisation."""
    if not text:
        return text

    def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
        original = m.group(0)
        corrected = _PTBR_ACCENT_MAP[original.lower()]
        return _restore_case(original, corrected)

    return _PTBR_ACCENT_RE.sub(_replace, text)


def format_brl(value: float | int | None) -> str:
    amount = 0.0 if value is None else float(value)
    formatted = f"{amount:,.2f}"
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R${formatted}"


def format_ptbr_date(value: Any) -> str | None:
    if not value:
        return None

    if hasattr(value, "strftime"):
        try:
            return value.strftime("%d-%m-%Y")
        except Exception:
            pass

    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue

    return text
