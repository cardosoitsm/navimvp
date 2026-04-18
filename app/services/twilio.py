import requests

from app.config import get_settings

_WHATSAPP_MAX_CHARS = 1500


def responder(numero: str, mensagem: str) -> None:
    settings = get_settings()
    if not settings.account_sid or not settings.auth_token or not settings.twilio_number:
        return

    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.account_sid}/Messages.json"
    )

    data = {
        "From": settings.twilio_number,
        "To": numero,
        "Body": mensagem,
    }

    requests.post(url, data=data, auth=(settings.account_sid, settings.auth_token), timeout=15)


def _split_message(text: str, max_len: int = _WHATSAPP_MAX_CHARS) -> list[str]:
    if len(text) <= max_len:
        return [text]

    parts: list[str] = []
    current = ""

    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}".strip() if current else para
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                parts.append(current)
            if len(para) <= max_len:
                current = para
            else:
                # Split oversized paragraph at line boundaries
                for line in para.split("\n"):
                    candidate = f"{current}\n{line}".strip() if current else line
                    if len(candidate) <= max_len:
                        current = candidate
                    else:
                        if current:
                            parts.append(current)
                        current = line[:max_len]

    if current:
        parts.append(current)

    return parts or [text[:max_len]]


def build_twiml(message: str | list[str]) -> str:
    if isinstance(message, list):
        chunks: list[str] = []
        for msg in message:
            chunks.extend(_split_message(msg))
    else:
        chunks = _split_message(message)
    msg_tags = "".join(
        f"<Message>{c.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')}</Message>"
        for c in chunks
    )
    return f"<Response>{msg_tags}</Response>"
