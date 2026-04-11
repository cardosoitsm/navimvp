from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.auth import create_token, get_current_user
from app.config import get_settings
from app.schemas import Message, User
from app.services.chat import process_user_message
from app.services.summary import resumo_categoria, resumo_mes
from app.services.twilio import build_twiml
from app.services.users import (
    authenticate_user,
    get_or_create_whatsapp_user,
    register_user,
)

settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.app_debug)


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return """
    <html>
      <head><meta charset="utf-8"><title>Navi MVP</title></head>
      <body>
        <h1>Navi MVP</h1>
        <p>API FastAPI ativa.</p>
      </body>
    </html>
    """


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    form = await request.form()
    mensagem = (form.get("Body") or "").strip()
    numero = (form.get("From") or "").strip()

    if not numero:
        return Response(
            content=build_twiml("Nao consegui identificar o remetente."),
            media_type="application/xml",
            status_code=400,
        )

    user_id, novo = get_or_create_whatsapp_user(numero)

    if novo:
        resposta = (
            "Ola! Bem-vindo ao Navi!\n\n"
            "Eu vou te ajudar a controlar seus gastos de forma simples.\n\n"
            "Voce pode me mandar mensagens como:\n"
            '"Gastei R$50 em Uber"\n'
            '"Quanto gastei em alimentacao?"\n'
            '"Quanto gastei no mes?"\n\n'
            "Vamos comecar? Me envie sua primeira transacao!"
        )
        return Response(content=build_twiml(resposta), media_type="application/xml")

    msg_lower = mensagem.lower()
    try:
        if "quanto gastei" in msg_lower and "transporte" in msg_lower:
            resposta = resumo_categoria(user_id, "transporte")
        elif "quanto gastei" in msg_lower and "alimentacao" in msg_lower:
            resposta = resumo_categoria(user_id, "alimentacao")
        elif "quanto gastei" in msg_lower:
            resposta = resumo_mes(user_id)
        else:
            resposta = process_user_message(mensagem, user_id)["resposta"]
    except HTTPException as exc:
        resposta = exc.detail
    except Exception:
        resposta = "Tive um problema para processar sua mensagem. Tente novamente em instantes."

    return Response(content=build_twiml(resposta), media_type="application/xml")


@app.api_route("/webhook-fallback", methods=["GET", "POST"])
def webhook_fallback() -> Response:
    resposta = "Estamos com uma instabilidade temporaria. Tente novamente em instantes."
    return Response(content=build_twiml(resposta), media_type="application/xml")


@app.post("/register")
def register(user: User) -> dict[str, int]:
    user_id = register_user(user.email, user.senha)
    return {"user_id": user_id}


@app.post("/login")
def login(user: User) -> dict[str, str]:
    user_id = authenticate_user(user.email, user.senha)
    return {"token": create_token(user_id)}


@app.post("/chat")
def chat(msg: Message, user_id: int = Depends(get_current_user)) -> dict[str, str]:
    return process_user_message(msg.text, user_id)
