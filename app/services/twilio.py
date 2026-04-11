import requests

from app.config import get_settings


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


def build_twiml(message: str) -> str:
    safe_message = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<Response><Message>{safe_message}</Message></Response>"
