from datetime import datetime
from typing import Any


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
