from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.auth import create_token, get_current_user
from app.config import get_settings
from app.db import init_db, ping_db
from app.schemas import AdminResetRequest, Message, User
from app.services.budgets import (
    build_budget_setup_confirmation,
    build_budget_status_message,
    extract_budget_category,
    is_budget_onboarding_completed,
    mark_budget_onboarding_completed,
    onboarding_budget_prompt,
    parse_budget_message,
    save_budgets,
    should_skip_budget_onboarding,
)
from app.services.conversation import (
    confirm_pending_transaction,
    detect_intent,
    normalize_text,
    reject_pending_transaction,
)
from app.services.chat import process_user_message
from app.services.summary import listar_ultimas_transacoes, resumo_categoria, resumo_mes
from app.services.twilio import build_twiml
from app.services.users import (
    authenticate_user,
    delete_user_account,
    get_or_create_whatsapp_user,
    register_user,
)

settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.app_debug)


@app.on_event("startup")
def startup() -> None:
    init_db()


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
    db_status = "ok" if ping_db() else "error"
    status = "ok" if db_status == "ok" else "degraded"
    return {"status": status, "environment": settings.app_env, "database": db_status}


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
            "Eu vou te ajudar a controlar seus gastos de forma simples e te avisar quando seus gastos sairem do planejado.\n\n"
            f"{onboarding_budget_prompt()}"
        )
        return Response(content=build_twiml(resposta), media_type="application/xml")

    msg_lower = normalize_text(mensagem)
    intent = detect_intent(mensagem)

    if not is_budget_onboarding_completed(user_id):
        budgets = parse_budget_message(mensagem)
        if budgets:
            save_budgets(user_id, budgets)
            resposta = build_budget_setup_confirmation(budgets)
            return Response(content=build_twiml(resposta), media_type="application/xml")
        if should_skip_budget_onboarding(mensagem):
            mark_budget_onboarding_completed(user_id)
            resposta = "Tudo bem. Voce pode configurar seus limites depois. Agora me envie sua primeira transacao."
            return Response(content=build_twiml(resposta), media_type="application/xml")
        if intent not in {"transaction", "summary", "recent_transactions", "confirm_yes", "confirm_no", "budget_status"}:
            resposta = onboarding_budget_prompt()
            return Response(content=build_twiml(resposta), media_type="application/xml")
        mark_budget_onboarding_completed(user_id)

    try:
        if intent == "confirm_yes":
            resposta = confirm_pending_transaction(user_id)["resposta"]
        elif intent == "confirm_no":
            resposta = reject_pending_transaction(user_id)
        elif intent == "recent_transactions":
            resposta = listar_ultimas_transacoes(user_id)
        elif intent == "budget_status":
            categoria = extract_budget_category(mensagem)
            if categoria:
                resposta = build_budget_status_message(user_id, categoria)
            else:
                resposta = "Me diga a categoria que voce quer consultar, por exemplo: Quanto ainda posso gastar com farmacia?"
        elif "quanto gastei" in msg_lower and "transporte" in msg_lower:
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


@app.post("/admin/reset-user")
def admin_reset_user(payload: AdminResetRequest, request: Request) -> dict[str, bool]:
    admin_key = (request.headers.get("X-Admin-Key") or "").strip()
    if admin_key != settings.secret_key:
        raise HTTPException(status_code=403, detail="Chave administrativa invalida")

    deleted = delete_user_account(payload.email)
    return {"deleted": deleted}
