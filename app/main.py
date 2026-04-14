from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
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
from app.services.documents import (
    complete_document_onboarding,
    document_invite_prompt,
    document_upload_prompt,
    get_incoming_media,
    is_document_onboarding_completed,
    is_waiting_for_document,
    process_stored_document,
    register_received_document,
    should_skip_document_onboarding,
    should_start_document_onboarding,
    start_document_onboarding,
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
async def webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
    form = await request.form()
    mensagem = (form.get("Body") or "").strip()
    numero = (form.get("From") or "").strip()
    incoming_media = get_incoming_media(form)

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

    def _register_document_upload() -> str:
        media_url, media_content_type = incoming_media  # type: ignore[misc]
        document_id, hinted_type, resposta = register_received_document(
            user_id,
            media_url,
            media_content_type,
            mensagem,
        )
        background_tasks.add_task(
            process_stored_document,
            document_id,
            user_id,
            media_url,
            media_content_type,
            mensagem,
            hinted_type,
        )
        return resposta

    msg_lower = normalize_text(mensagem)
    intent = detect_intent(mensagem)

    if not is_budget_onboarding_completed(user_id):
        if incoming_media:
            _register_document_upload()
            resposta = (
                "Recebi seu documento e vou guardar esse material.\n\n"
                f"Antes de continuar, {onboarding_budget_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        budgets = parse_budget_message(mensagem)
        if budgets:
            save_budgets(user_id, budgets)
            resposta = (
                f"{build_budget_setup_confirmation(budgets)}\n\n"
                f"{document_invite_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        if should_skip_budget_onboarding(mensagem):
            mark_budget_onboarding_completed(user_id)
            resposta = "Tudo bem. Voce pode configurar seus limites depois. Agora me envie sua primeira transacao."
            return Response(content=build_twiml(resposta), media_type="application/xml")
        resposta = (
            "Ainda nao consegui registrar seus limites mensais.\n\n"
            f"{onboarding_budget_prompt()}"
        )
        return Response(content=build_twiml(resposta), media_type="application/xml")

    if not is_document_onboarding_completed(user_id):
        if is_waiting_for_document(user_id):
            try:
                if incoming_media:
                    resposta = _register_document_upload()
                    complete_document_onboarding(user_id)
                    return Response(content=build_twiml(resposta), media_type="application/xml")
                if should_skip_document_onboarding(mensagem):
                    complete_document_onboarding(user_id)
                    resposta = "Tudo bem. Podemos analisar seus documentos depois. Agora ja posso seguir com o seu acompanhamento financeiro."
                    return Response(content=build_twiml(resposta), media_type="application/xml")
                resposta = document_upload_prompt()
                return Response(content=build_twiml(resposta), media_type="application/xml")
            except HTTPException as exc:
                return Response(content=build_twiml(exc.detail), media_type="application/xml")
            except Exception:
                resposta = (
                    "Recebi seu documento, mas tive um problema para processa-lo agora. "
                    "Tente enviar novamente em instantes."
                )
                return Response(content=build_twiml(resposta), media_type="application/xml")

        if should_start_document_onboarding(mensagem):
            start_document_onboarding(user_id)
            resposta = document_upload_prompt()
            return Response(content=build_twiml(resposta), media_type="application/xml")
        if should_skip_document_onboarding(mensagem):
            complete_document_onboarding(user_id)
            resposta = "Tudo bem. Podemos analisar seus documentos depois. Agora ja posso seguir com o seu acompanhamento financeiro."
            return Response(content=build_twiml(resposta), media_type="application/xml")
        resposta = document_invite_prompt()
        return Response(content=build_twiml(resposta), media_type="application/xml")

    try:
        if incoming_media:
            resposta = _register_document_upload()
            resposta = (
                f"{resposta}\n\n"
                "Se quiser me ajudar a interpretar melhor, voce tambem pode escrever junto se isso e extrato ou fatura."
            )
        elif intent == "confirm_yes":
            resposta = confirm_pending_transaction(user_id)["resposta"]
        elif intent == "confirm_no":
            resposta = reject_pending_transaction(user_id)
        elif intent == "document_request":
            start_document_onboarding(user_id)
            resposta = (
                "Posso te ajudar com esse documento.\n\n"
                f"{document_upload_prompt()}"
            )
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
