from datetime import datetime
from typing import Any


def format_monetary(value: float | int | None, currency: str = "BRL", user_locale: str = "pt-BR") -> str:
    """Format a monetary value according to the user's locale and the given ISO 4217 currency code.

    Delegates entirely to Babel — no separators or symbols are hardcoded here.
    Falls back to a neutral representation on any error.
    """
    if value is None:
        return ""
    try:
        from babel.numbers import format_currency  # noqa: PLC0415
        return format_currency(float(value), currency.upper(), locale=user_locale.replace("-", "_"))
    except Exception:
        return f"{currency} {float(value):.2f}"


def format_date(date_str: str | None, user_locale: str = "pt-BR") -> str | None:
    """Format an ISO date string (YYYY-MM-DD) using the user's locale.

    Delegates entirely to Babel — no date format is hardcoded here.
    Falls back to the ISO string on any error.
    """
    if not date_str:
        return None
    try:
        from datetime import date as _date  # noqa: PLC0415
        from babel.dates import format_date as _babel_fmt  # noqa: PLC0415
        d = _date.fromisoformat(str(date_str).strip())
        return _babel_fmt(d, format="short", locale=user_locale.replace("-", "_"))
    except Exception:
        return str(date_str)


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
