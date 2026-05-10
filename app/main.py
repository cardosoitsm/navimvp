import logging
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.auth import create_token, get_current_user
from app.config import get_settings
from app.services.logger import get_logger
from app.db import init_db, ping_db
from app.schemas import AdminResetRequest, Message, User
from app.services.budgets import (
    budget_edit_prompt,
    build_all_budgets_status_message,
    build_budget_setup_confirmation,
    build_budget_status_message,
    extract_budget_category,
    is_budget_edit_request,
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
    _build_adjustments_applied_message,
    _build_analysis_message,
    apply_user_adjustments,
    build_invoice_status_message,
    build_onboarding_completion_message,
    complete_document_onboarding,
    document_invite_prompt,
    document_invite_retry_prompt,
    document_upload_prompt,
    document_upload_retry_prompt,
    get_incoming_media,
    get_latest_extrato_analysis,
    get_latest_fatura_analysis,
    has_document_type,
    is_document_processing,
    is_invoice_followup_message,
    is_document_onboarding_completed,
    is_waiting_for_document,
    mark_extrato_reviewed,
    mark_fatura_reviewed,
    process_stored_document,
    register_received_document,
    save_extrato_adjustments,
    should_skip_document_onboarding,
    should_start_document_onboarding,
    start_document_onboarding,
)
from app.services.financial_analysis import (
    build_affordability_message,
    build_loan_evaluation_message,
    build_recommendations_message,
)
from app.services.financial_health import build_financial_health_message
from app.services.voice import is_audio_media, transcribe_audio
from app.services.onboarding import (
    USER_REGISTRATION_PENDING,
    ACCOUNT_SNAPSHOT_PENDING,
    STATEMENT_REVIEW_PENDING,
    BUDGET_SETUP_PENDING,
    CARD_COUNT_PENDING,
    COST_REVIEW_PENDING,
    CARD_INVOICE_PENDING,
    INVOICE_REVIEW_PENDING,
    CARD_NAMES_PENDING,
    DOCUMENT_ONBOARDING_PENDING,
    advance_card_progress,
    build_card_setup_confirmation,
    build_cost_review_confirmation,
    ONBOARDING_COMPLETE,
    account_snapshot_prompt,
    account_snapshot_retry_prompt,
    budget_setup_retry_prompt,
    card_count_prompt,
    card_count_retry_prompt,
    card_invoice_prompt,
    card_invoice_retry_prompt,
    card_names_prompt,
    card_names_retry_prompt,
    cost_review_adjustment_prompt,
    cost_review_prompt,
    cost_review_retry_prompt,
    has_cost_review_candidates,
    infer_invoice_cost_candidates,
    get_current_card,
    get_card_names,
    get_onboarding_state,
    get_pending_card_total,
    infer_cost_candidates,
    is_confirmation_no,
    is_confirmation_yes,
    is_cost_review_completed,
    is_statement_already_reviewed,
    parse_card_count,
    parse_card_names_llm_first,
    parse_balance_message,
    parse_cost_review_adjustments,
    save_card_count,
    save_cost_candidates,
    save_card_names,
    save_current_balance,
    set_pending_card_index,
    set_onboarding_state,
    get_user_name,
    parse_user_name,
    registration_prompt,
    registration_retry_prompt,
    save_user_name,
    should_skip_account_snapshot,
    should_skip_card_setup,
)
from app.services.chat import process_user_message
from app.services.help import (
    build_help_menu,
    clear_help_context,
    get_help_content,
    is_help_menu_active,
    parse_help_selection,
    save_help_context,
)
from app.services.summary import listar_ultimas_transacoes, resumo_categoria, resumo_mes
from app.services.twilio import build_twiml, responder
from app.services.users import (
    authenticate_user,
    delete_user_account,
    get_or_create_whatsapp_user,
    register_user,
)

settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.app_debug)

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
logger = get_logger("navi.webhook")


@app.on_event("startup")
def startup() -> None:
    logging.basicConfig(level=logging.INFO)
    init_db()
    logger.info("startup env=%s", settings.app_env)


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    """Serve the Navi registration and login front-end."""
    index_path = Path(__file__).parent / "static" / "index.html"
    return index_path.read_text(encoding="utf-8")


@app.get("/health")
def healthcheck() -> dict[str, str]:
    db_status = "ok" if ping_db() else "error"
    status = "ok" if db_status == "ok" else "degraded"
    return {"status": status, "environment": settings.app_env, "database": db_status}


@app.post("/debug/test-responder")
def debug_test_responder(numero: str, mensagem: str) -> dict[str, str]:
    try:
        responder(numero, mensagem)
        return {"status": "ok", "to": numero}
    except Exception as exc:
        return {"status": "error", "to": numero, "detail": str(exc)}


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
    form = await request.form()
    mensagem = (form.get("Body") or "").strip()
    numero = (form.get("From") or "").strip()
    incoming_media = get_incoming_media(form)

    # Transcreve áudio (mensagens de voz do WhatsApp) antes do processamento normal
    if incoming_media:
        _, media_content_type = incoming_media
        if is_audio_media(media_content_type):
            transcript = transcribe_audio(incoming_media[0])
            if transcript:
                mensagem = transcript
                incoming_media = None
            else:
                return Response(
                    content=build_twiml(
                        "Recebi seu áudio, mas não consegui transcrever agora. "
                        "Se puder, tente enviar a mensagem por texto."
                    ),
                    media_type="application/xml",
                )

    if not numero:
        return Response(
            content=build_twiml("Não consegui identificar o remetente."),
            media_type="application/xml",
            status_code=400,
        )

    user_id, novo = get_or_create_whatsapp_user(numero)

    if novo:
        _nome = parse_user_name(mensagem)
        if _nome:
            save_user_name(user_id, _nome)
            _name_greeting = f"Prazer, {_nome}! Vou usar esse nome para te chamar por aqui.\n\n"
        else:
            _name_greeting = ""
        set_onboarding_state(user_id, ACCOUNT_SNAPSHOT_PENDING)
        resposta = (
            "Olá! Que bom ter você por aqui.\n\n"
            "Eu sou o Navi e vou te ajudar a acompanhar seus gastos de um jeito leve, sem complicação.\n\n"
            f"{_name_greeting}{account_snapshot_prompt()}"
        )
        return Response(content=build_twiml(resposta), media_type="application/xml")

    def _register_document_upload(
        forced_type: str | None = None,
        on_complete_numero: str | None = None,
        on_complete_state: str | None = None,
    ) -> str:
        media_url, media_content_type = incoming_media  # type: ignore[misc]
        current_card = get_current_card(user_id) if forced_type == "fatura_cartao" else None
        document_id, hinted_type, resposta = register_received_document(
            user_id,
            media_url,
            media_content_type,
            mensagem,
            forced_type,
            int(current_card["id"]) if current_card and current_card.get("id") is not None else None,
        )
        background_tasks.add_task(
            process_stored_document,
            document_id,
            user_id,
            media_url,
            media_content_type,
            mensagem,
            hinted_type,
            int(current_card["id"]) if current_card and current_card.get("id") is not None else None,
            on_complete_numero,
            on_complete_state,
        )
        return resposta

    msg_lower = normalize_text(mensagem)
    intent = detect_intent(mensagem)
    if intent == "transaction" and is_invoice_followup_message(user_id, mensagem):
        intent = "invoice_status"
    if intent == "transaction" and not incoming_media and is_help_menu_active(user_id):
        selection = parse_help_selection(mensagem)
        if selection is not None:
            intent = "help_selection"
    logger.info("webhook user_id=%s intent=%s media=%s", user_id, intent, bool(incoming_media))
    onboarding_state = get_onboarding_state(user_id)
    existing_card_names = get_card_names(user_id)

    # BUG-E2E-002 FIX: query intents must bypass onboarding state handlers.
    # Users in any onboarding state should always be able to query spending/health.
    _QUERY_INTENTS = frozenset({"financial_health", "recent_transactions", "budget_status"})
    _is_query = intent in _QUERY_INTENTS or "quanto gastei" in msg_lower
    if onboarding_state == USER_REGISTRATION_PENDING:
        nome = parse_user_name(mensagem)
        if nome:
            save_user_name(user_id, nome)
            set_onboarding_state(user_id, ACCOUNT_SNAPSHOT_PENDING)
            resposta = (
                f"Prazer, {nome}! Vou usar esse nome para te chamar por aqui.\n\n"
                f"{account_snapshot_prompt()}"
            )
        else:
            resposta = registration_retry_prompt()
        return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == ACCOUNT_SNAPSHOT_PENDING and not _is_query:
        if incoming_media:
            _register_document_upload(
                "extrato",
                on_complete_numero=numero,
                on_complete_state=STATEMENT_REVIEW_PENDING,
            )
            set_onboarding_state(user_id, STATEMENT_REVIEW_PENDING)
            resposta = (
                "Recebi seu extrato. Estou analisando os lançamentos agora, isso leva alguns instantes...\n\n"
                "Assim que terminar, te mando a lista completa para você revisar antes de continuarmos."
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        balance = parse_balance_message(mensagem)
        if balance is not None:
            save_current_balance(user_id, balance)
            set_onboarding_state(user_id, BUDGET_SETUP_PENDING)
            resposta = (
                f"Perfeito. Anotei seu saldo atual em R${balance:.2f}.\n\n"
                "Isso já me dá um bom ponto de partida para te orientar melhor.\n\n"
                f"{onboarding_budget_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        if should_skip_account_snapshot(mensagem):
            set_onboarding_state(user_id, BUDGET_SETUP_PENDING)
            resposta = (
                "Tudo bem. A gente pode voltar para esse retrato inicial depois.\n\n"
                f"{onboarding_budget_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        if intent == "document_request":
            resposta = (
                "Claro. Pode me enviar o extrato de hoje agora mesmo.\n\n"
                "Se for mais fácil, você também pode simplesmente me dizer o saldo atual da sua conta."
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        if intent == "card_setup_request":
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = (
                "Claro. Vamos organizar seus cartões por aqui também.\n\n"
                f"{card_count_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        resposta = account_snapshot_retry_prompt()
        return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == STATEMENT_REVIEW_PENDING:
        if is_document_processing(user_id):
            resposta = "Ainda estou analisando seu extrato, aguarde um momento..."
            return Response(content=build_twiml(resposta), media_type="application/xml")

        if is_confirmation_yes(msg_lower):
            mark_extrato_reviewed(user_id)
            set_onboarding_state(user_id, BUDGET_SETUP_PENDING)
            resposta = (
                "Ótimo! Lançamentos confirmados.\n\n"
                f"{onboarding_budget_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        analysis = get_latest_extrato_analysis(user_id)
        if not analysis:
            set_onboarding_state(user_id, BUDGET_SETUP_PENDING)
            resposta = (
                "Não consegui extrair os lançamentos do seu extrato.\n\n"
                f"{onboarding_budget_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        existing_rows = analysis.get("statement_rows") or []
        if existing_rows and mensagem:
            updated_rows, adj_error = apply_user_adjustments(mensagem, existing_rows, user_id)
            if adj_error:
                resposta = adj_error
            elif updated_rows != existing_rows:
                analysis["statement_rows"] = updated_rows
                save_extrato_adjustments(user_id, updated_rows, tipo_documento="extrato")
                resposta = _build_adjustments_applied_message(updated_rows, existing_rows)
            else:
                resposta = _build_adjustments_applied_message(existing_rows, existing_rows)
        else:
            resposta = _build_analysis_message(analysis, "extrato")
        return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == INVOICE_REVIEW_PENDING:
        if is_document_processing(user_id):
            resposta = "Ainda estou analisando sua fatura, aguarde um momento..."
            return Response(content=build_twiml(resposta), media_type="application/xml")

        if is_confirmation_yes(msg_lower):
            mark_fatura_reviewed(user_id)
            _reviewed = is_statement_already_reviewed(user_id)
            next_state = (
                COST_REVIEW_PENDING
                if has_cost_review_candidates(user_id) and not is_cost_review_completed(user_id) and not _reviewed
                else CARD_COUNT_PENDING
            )
            set_onboarding_state(user_id, next_state)
            resposta = "Ótimo! Lançamentos da fatura confirmados."
            return Response(content=build_twiml(resposta), media_type="application/xml")

        analysis = get_latest_fatura_analysis(user_id)
        if not analysis:
            _reviewed = is_statement_already_reviewed(user_id)
            next_state = (
                COST_REVIEW_PENDING
                if has_cost_review_candidates(user_id) and not is_cost_review_completed(user_id) and not _reviewed
                else CARD_COUNT_PENDING
            )
            set_onboarding_state(user_id, next_state)
            resposta = "Não consegui extrair os lançamentos da fatura."
            return Response(content=build_twiml(resposta), media_type="application/xml")

        existing_rows = analysis.get("statement_rows") or []
        if existing_rows and mensagem:
            updated_rows, adj_error = apply_user_adjustments(mensagem, existing_rows, user_id)
            if adj_error:
                resposta = adj_error
            elif updated_rows != existing_rows:
                analysis["statement_rows"] = updated_rows
                save_extrato_adjustments(user_id, updated_rows, tipo_documento="fatura_cartao")
                resposta = _build_adjustments_applied_message(updated_rows, existing_rows)
            else:
                resposta = _build_adjustments_applied_message(existing_rows, existing_rows)
        else:
            resposta = _build_analysis_message(analysis, "fatura_cartao")
        return Response(content=build_twiml(resposta), media_type="application/xml")

    if (onboarding_state == BUDGET_SETUP_PENDING or not is_budget_onboarding_completed(user_id)) and not _is_query:
        if is_waiting_for_document(user_id):
            try:
                if incoming_media:
                    resposta = _register_document_upload()
                    complete_document_onboarding(user_id)
                    resposta = (
                        f"{resposta}\n\n"
                        "Se quiser, depois ainda podemos configurar seus limites mensais com calma. "
                        'Basta me dizer seus limites ou responder "PULAR" para seguir sem isso por enquanto.'
                    )
                    return Response(content=build_twiml(resposta), media_type="application/xml")
                if should_skip_budget_onboarding(mensagem):
                    mark_budget_onboarding_completed(user_id, None)
                    resposta = (
                        "Sem problema. Vamos deixar seus limites para depois.\n\n"
                        f"{document_upload_prompt(user_id)}"
                    )
                    return Response(content=build_twiml(resposta), media_type="application/xml")
                resposta = (
                    "Claro, podemos começar por esse documento.\n\n"
                    f"{document_upload_prompt(user_id)}\n\n"
                    'Se em algum momento quiser voltar aos limites, me mande algo como "Farmacia 290, mercado 1200".'
                )
                return Response(content=build_twiml(resposta), media_type="application/xml")
            except HTTPException as exc:
                return Response(content=build_twiml(exc.detail), media_type="application/xml")
            except Exception:
                resposta = (
                    "Recebi seu documento, mas tive um problema para processá-lo agora. "
                    "Se puder, tente de novo daqui a pouco."
                )
                return Response(content=build_twiml(resposta), media_type="application/xml")
        if incoming_media:
            _register_document_upload()
            resposta = (
                "Recebi seu documento e já deixei isso guardado aqui.\n\n"
                f"Antes de seguir, {onboarding_budget_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        if intent == "document_request":
            start_document_onboarding(user_id)
            resposta = (
                "Consigo sim. Vamos fazer isso agora.\n\n"
                f"{document_upload_prompt(user_id)}\n\n"
                'Se preferir, depois a gente volta para seus limites. E se quiser pular essa etapa por enquanto, responda "PULAR".'
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        if intent == "card_setup_request":
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = (
                "Claro. Vamos organizar seus cartões antes de seguir.\n\n"
                f"{card_count_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        budgets = parse_budget_message(mensagem)
        if budgets:
            _reviewed = is_statement_already_reviewed(user_id)
            next_state = COST_REVIEW_PENDING if has_cost_review_candidates(user_id) and not is_cost_review_completed(user_id) and not _reviewed else CARD_COUNT_PENDING
            if _reviewed and next_state == CARD_COUNT_PENDING and not _is_query:
                logger.info("cost_review_skipped_already_reviewed user_id=%s", user_id)
            save_budgets(user_id, budgets, next_state)
            if next_state == COST_REVIEW_PENDING and not _is_query:
                fixed_costs, variable_costs = infer_cost_candidates(user_id)
                resposta = (
                    f"{build_budget_setup_confirmation(budgets)}\n\n"
                    f"{cost_review_prompt(fixed_costs, variable_costs)}"
                )
            else:
                resposta = (
                    f"{build_budget_setup_confirmation(budgets)}\n\n"
                    f"{card_count_prompt()}"
                )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        if is_budget_edit_request(mensagem):
            resposta = budget_edit_prompt()
            return Response(content=build_twiml(resposta), media_type="applichation/xml")
        if should_skip_budget_onboarding(mensagem):
            _reviewed = is_statement_already_reviewed(user_id)
            next_state = COST_REVIEW_PENDING if has_cost_review_candidates(user_id) and not is_cost_review_completed(user_id) and not _reviewed else CARD_COUNT_PENDING
            if _reviewed and next_state == CARD_COUNT_PENDING and not _is_query:
                logger.info("cost_review_skipped_already_reviewed user_id=%s", user_id)
            mark_budget_onboarding_completed(user_id, next_state)
            if next_state == COST_REVIEW_PENDING and not _is_query:
                fixed_costs, variable_costs = infer_cost_candidates(user_id)
                resposta = (
                    "Tudo bem. A gente pode configurar seus limites depois.\n\n"
                    f"{cost_review_prompt(fixed_costs, variable_costs)}"
                )
            else:
                resposta = (
                    "Tudo bem. A gente pode configurar seus limites depois.\n\n"
                    f"{card_count_prompt()}"
                )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        resposta = budget_setup_retry_prompt()
        return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == COST_REVIEW_PENDING and not _is_query:
        is_post_cards = bool(existing_card_names)
        if is_post_cards and is_document_processing(user_id):
            resposta = "Ainda estou processando sua fatura, aguarde um momento..."
            return Response(content=build_twiml(resposta), media_type="application/xml")

        if not is_post_cards and is_statement_already_reviewed(user_id):
            logger.info("cost_review_skipped_already_reviewed user_id=%s", user_id)
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = (
                "Seus lançamentos já foram organizados. Seguindo em frente...\n\n"
                f"{card_count_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        if is_post_cards:
            fixed_costs, variable_costs = infer_invoice_cost_candidates(user_id)
        else:
            fixed_costs, variable_costs = infer_cost_candidates(user_id)

        if not fixed_costs and not variable_costs:
            if is_post_cards:
                complete_document_onboarding(user_id)
                summary_msg, ready_msg = build_onboarding_completion_message(user_id)
                return Response(content=build_twiml([summary_msg, ready_msg]), media_type="application/xml")
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = card_count_prompt()
            return Response(content=build_twiml(resposta), media_type="application/xml")

        origem = "fatura" if is_post_cards else "extrato"

        if should_skip_budget_onboarding(mensagem):
            save_cost_candidates(user_id, confirmed=False, fixed_costs=fixed_costs, variable_costs=variable_costs, origem=origem)
            if is_post_cards:
                complete_document_onboarding(user_id)
                skip_msg = "Tudo bem. A gente pode revisar esses custos com calma mais para frente."
                summary_msg, ready_msg = build_onboarding_completion_message(user_id)
                return Response(content=build_twiml([skip_msg, summary_msg, ready_msg]), media_type="application/xml")
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = (
                "Tudo bem. A gente pode revisar esses custos com calma mais para frente.\n\n"
                f"{card_count_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        adjusted_costs = parse_cost_review_adjustments(mensagem, fixed_costs, variable_costs)
        if adjusted_costs:
            adjusted_fixed, adjusted_variable = adjusted_costs
            save_cost_candidates(
                user_id,
                confirmed=True,
                fixed_costs=adjusted_fixed,
                variable_costs=adjusted_variable,
                origem=origem,
            )
            if is_post_cards:
                complete_document_onboarding(user_id)
                summary_msg, ready_msg = build_onboarding_completion_message(user_id)
                confirmation_msg = build_cost_review_confirmation(adjusted_fixed, adjusted_variable)
                return Response(content=build_twiml([confirmation_msg, summary_msg, ready_msg]), media_type="application/xml")
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = (
                f"{build_cost_review_confirmation(adjusted_fixed, adjusted_variable)}\n\n"
                f"{card_count_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        if is_confirmation_yes(mensagem):
            save_cost_candidates(user_id, confirmed=True, fixed_costs=fixed_costs, variable_costs=variable_costs, origem=origem)
            if is_post_cards:
                complete_document_onboarding(user_id)
                summary_msg, ready_msg = build_onboarding_completion_message(user_id)
                confirm_msg = "Perfeito. Vou considerar essa leitura dos seus custos para te acompanhar melhor."
                return Response(content=build_twiml([confirm_msg, summary_msg, ready_msg]), media_type="application/xml")
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = (
                "Perfeito. Vou considerar essa leitura inicial dos seus custos para te acompanhar melhor.\n\n"
                f"{card_count_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        if is_confirmation_no(mensagem):
            resposta = cost_review_adjustment_prompt()
            return Response(content=build_twiml(resposta), media_type="application/xml")

        resposta = cost_review_retry_prompt()
        return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == CARD_COUNT_PENDING and not _is_query:
        try:
            if should_skip_card_setup(mensagem):
                save_card_count(user_id, 0)
                if has_document_type(user_id, "extrato"):
                    set_onboarding_state(user_id, ONBOARDING_COMPLETE)
                    resposta = (
                        "Tudo bem. Como eu já tenho seu extrato, isso já me dá uma boa base inicial para te acompanhar.\n\n"
                        "Se depois você quiser cadastrar algum cartão, eu organizo isso com você."
                    )
                else:
                    set_onboarding_state(user_id, DOCUMENT_ONBOARDING_PENDING)
                    resposta = (
                        "Tudo bem. A gente pode cadastrar seus cartões depois.\n\n"
                        f"{document_invite_prompt(user_id)}"
                    )
                return Response(content=build_twiml(resposta), media_type="application/xml")

            card_total = parse_card_count(mensagem)
            if card_total is not None:
                save_card_count(user_id, card_total)
                if card_total <= 0:
                    if has_document_type(user_id, "extrato"):
                        set_onboarding_state(user_id, ONBOARDING_COMPLETE)
                        resposta = (
                            "Perfeito. Entendi que você não quer acompanhar cartões por agora.\n\n"
                            "Como eu já tenho seu extrato, já consigo seguir com uma boa base inicial."
                        )
                    else:
                        set_onboarding_state(user_id, DOCUMENT_ONBOARDING_PENDING)
                        resposta = (
                            "Perfeito. Entendi que você não quer acompanhar cartões por agora.\n\n"
                            f"{document_invite_prompt(user_id)}"
                        )
                    return Response(content=build_twiml(resposta), media_type="application/xml")

                set_onboarding_state(user_id, CARD_NAMES_PENDING)
                resposta = card_names_prompt(card_total)
                return Response(content=build_twiml(resposta), media_type="application/xml")

            if intent == "document_request":
                resposta = (
                    "Consigo sim. Antes, só me conta quantos cartões você quer acompanhar comigo, que eu organizo isso certinho.\n\n"
                    f"{card_count_prompt()}"
                )
                return Response(content=build_twiml(resposta), media_type="application/xml")

            resposta = card_count_retry_prompt()
            return Response(content=build_twiml(resposta), media_type="application/xml")
        except HTTPException as exc:
            return Response(content=build_twiml(exc.detail), media_type="application/xml")
        except Exception:
            resposta = (
                "Tive um problema para anotar essa etapa dos cartões agora. "
                "Se puder, tente me responder novamente com a quantidade de cartões."
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == CARD_NAMES_PENDING and not _is_query:
        try:
            expected_count = get_pending_card_total(user_id)
            if expected_count <= 0:
                set_onboarding_state(user_id, DOCUMENT_ONBOARDING_PENDING)
                resposta = document_invite_prompt(user_id)
                return Response(content=build_twiml(resposta), media_type="application/xml")

            if should_skip_card_setup(mensagem):
                save_card_count(user_id, 0)
                if has_document_type(user_id, "extrato"):
                    set_onboarding_state(user_id, ONBOARDING_COMPLETE)
                    resposta = (
                        "Tudo bem. A gente pode deixar os cartões para depois.\n\n"
                        "Como eu já tenho seu extrato, isso já me ajuda bastante por enquanto."
                    )
                else:
                    set_onboarding_state(user_id, DOCUMENT_ONBOARDING_PENDING)
                    resposta = (
                        "Tudo bem. A gente pode deixar os cartões para depois.\n\n"
                        f"{document_invite_prompt(user_id)}"
                    )
                return Response(content=build_twiml(resposta), media_type="application/xml")

            card_names = parse_card_names_llm_first(mensagem, expected_count)
            if card_names:
                save_card_names(user_id, card_names)
                set_pending_card_index(user_id, 0)
                set_onboarding_state(user_id, CARD_INVOICE_PENDING)
                confirmed_names = get_card_names(user_id) or card_names
                resposta = (
                    f"{build_card_setup_confirmation(confirmed_names)}\n\n"
                    f"{card_invoice_prompt(confirmed_names[0])}"
                )
                return Response(content=build_twiml(resposta), media_type="application/xml")

            resposta = card_names_retry_prompt(expected_count)
            return Response(content=build_twiml(resposta), media_type="application/xml")
        except HTTPException as exc:
            return Response(content=build_twiml(exc.detail), media_type="application/xml")
        except Exception:
            resposta = (
                "Tive um problema para salvar os nomes dos seus cartões agora. "
                "Se puder, tente me mandar os nomes novamente separados por vírgula."
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == CARD_INVOICE_PENDING and not _is_query:
        try:
            current_card = get_current_card(user_id)
            if not current_card:
                complete_document_onboarding(user_id)
                resposta = "Perfeito. Já organizei essa etapa inicial e agora posso seguir com você normalmente."
                return Response(content=build_twiml(resposta), media_type="application/xml")

            if incoming_media:
                next_card = advance_card_progress(user_id)
                if next_card:
                    _register_document_upload("fatura_cartao")
                    resposta = (
                        f"Perfeito. Já deixei a fatura do {current_card['nome_cartao']} salva por aqui.\n\n"
                        "Isso já me ajuda a acompanhar melhor esse cartão e a deixar sua base financeira mais redonda.\n\n"
                        f"Agora me manda a fatura atual do {next_card['nome_cartao']}."
                    )
                else:
                    card_name_display = str(current_card["nome_cartao"])
                    _register_document_upload(
                        "fatura_cartao",
                        on_complete_numero=numero,
                        on_complete_state=INVOICE_REVIEW_PENDING,
                    )
                    resposta = (
                        f"Recebi sua fatura do {card_name_display}. "
                        "Estou analisando os lançamentos agora, isso leva alguns instantes...\n\n"
                        "Assim que terminar, te mando a lista completa para você revisar."
                    )
                return Response(content=build_twiml(resposta), media_type="application/xml")

            if should_skip_document_onboarding(mensagem):
                next_card = advance_card_progress(user_id)
                if next_card:
                    resposta = (
                        f"Tudo bem. Vamos deixar a fatura do {current_card['nome_cartao']} para depois.\n\n"
                        f"{card_invoice_prompt(str(next_card['nome_cartao']))}"
                    )
                else:
                    complete_document_onboarding(user_id)
                    resposta = "Tudo bem. Podemos completar as faturas depois. Por enquanto, sigo te ajudando com o restante."
                return Response(content=build_twiml(resposta), media_type="application/xml")

            resposta = card_invoice_retry_prompt(str(current_card["nome_cartao"]))
            return Response(content=build_twiml(resposta), media_type="application/xml")
        except HTTPException as exc:
            return Response(content=build_twiml(exc.detail), media_type="application/xml")
        except Exception:
            current_card = get_current_card(user_id)
            fallback_name = current_card["nome_cartao"] if current_card else "esse cartão"
            resposta = card_invoice_retry_prompt(str(fallback_name))
            return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state != ONBOARDING_COMPLETE and not is_document_onboarding_completed(user_id) and not _is_query:
        budgets = parse_budget_message(mensagem)
        if budgets:
            save_budgets(user_id, budgets, None)
            resposta = (
                "Perfeito. Atualizei seus limites.\n\n"
                f"{build_budget_setup_confirmation(budgets)}\n\n"
                f"{document_invite_prompt(user_id)}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        if is_budget_edit_request(mensagem):
            resposta = (
                f"{budget_edit_prompt()}\n\n"
                "Depois que você me mandar os novos valores, eu atualizo tudo por aqui."
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        if intent == "card_setup_request":
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = (
                "Perfeito. Vamos trazer seus cartões para dentro dessa organização.\n\n"
                f"{card_count_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        if incoming_media:
            try:
                resposta = _register_document_upload()
                complete_document_onboarding(user_id)
                return Response(content=build_twiml(resposta), media_type="application/xml")
            except HTTPException as exc:
                return Response(content=build_twiml(exc.detail), media_type="application/xml")
            except Exception:
                resposta = (
                    "Recebi seu documento, mas tive um problema para processá-lo agora. "
                    "Se puder, tente novamente daqui a pouco."
                )
                return Response(content=build_twiml(resposta), media_type="application/xml")
        if is_waiting_for_document(user_id):
            try:
                if incoming_media:
                    resposta = _register_document_upload()
                    complete_document_onboarding(user_id)
                    return Response(content=build_twiml(resposta), media_type="application/xml")
                if should_skip_document_onboarding(mensagem):
                    complete_document_onboarding(user_id)
                    resposta = "Tudo bem. Podemos olhar esses documentos depois. Por enquanto, sigo te ajudando com o restante."
                    return Response(content=build_twiml(resposta), media_type="application/xml")
                resposta = document_upload_retry_prompt()
                return Response(content=build_twiml(resposta), media_type="application/xml")
            except HTTPException as exc:
                return Response(content=build_twiml(exc.detail), media_type="application/xml")
            except Exception:
                resposta = (
                    "Recebi seu documento, mas tive um problema para processá-lo agora. "
                    "Se puder, tente novamente daqui a pouco."
                )
                return Response(content=build_twiml(resposta), media_type="application/xml")

        if should_start_document_onboarding(mensagem):
            start_document_onboarding(user_id)
            resposta = document_upload_prompt(user_id)
            return Response(content=build_twiml(resposta), media_type="application/xml")
        if should_skip_document_onboarding(mensagem):
            complete_document_onboarding(user_id)
            resposta = "Tudo bem. Podemos olhar seus documentos depois. Por enquanto, seguimos com o restante."
            return Response(content=build_twiml(resposta), media_type="application/xml")
        resposta = document_invite_retry_prompt()
        return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == ONBOARDING_COMPLETE and has_document_type(user_id, "extrato") and not existing_card_names:
        card_total = parse_card_count(mensagem)
        if card_total is not None:
            try:
                save_card_count(user_id, card_total)
                if card_total <= 0:
                    resposta = (
                        "Perfeito. Entendi que você não quer acompanhar cartões por agora.\n\n"
                        "Se depois mudar de ideia, eu organizo isso com você."
                    )
                else:
                    set_onboarding_state(user_id, CARD_NAMES_PENDING)
                    resposta = card_names_prompt(card_total)
                return Response(content=build_twiml(resposta), media_type="application/xml")
            except Exception:
                resposta = (
                    "Entendi que você quer cadastrar cartões, mas tive um problema para salvar essa etapa agora.\n\n"
                    f"{card_count_prompt()}"
                )
                return Response(content=build_twiml(resposta), media_type="application/xml")

    try:
        if incoming_media:
            resposta = _register_document_upload()
            resposta = (
                f"{resposta}\n\n"
                "Se quiser me ajudar a interpretar melhor, você também pode escrever se isso é um extrato ou uma fatura."
            )
        elif intent == "confirm_yes":
            resposta = confirm_pending_transaction(user_id)["resposta"]
        elif intent == "confirm_no":
            resposta = reject_pending_transaction(user_id)
        elif intent == "document_request":
            if onboarding_state == ONBOARDING_COMPLETE:
                resposta = (
                    "Claro. Pode me mandar agora um extrato ou fatura pelo WhatsApp.\n\n"
                    "Pode ser imagem ou PDF — assim que receber, já começo a analisar."
                )
            else:
                start_document_onboarding(user_id)
                resposta = (
                    "Claro. Posso te ajudar com esse documento.\n\n"
                    f"{document_upload_prompt(user_id)}"
                )
        elif intent == "invoice_status":
            resposta = build_invoice_status_message(user_id, mensagem)
        elif intent == "financial_health":
            resposta = build_financial_health_message(user_id)
        elif intent == "affordability_check":
            resposta = build_affordability_message(user_id, mensagem)
        elif intent == "loan_evaluation":
            resposta = build_loan_evaluation_message(user_id, mensagem)
        elif intent == "financial_recommendations":
            resposta = build_recommendations_message(user_id)
        elif intent == "help_request":
            save_help_context(user_id)
            resposta = build_help_menu()
        elif intent == "help_selection":
            selection = parse_help_selection(mensagem)
            content = get_help_content(selection) if selection else None
            if content:
                clear_help_context(user_id)
                resposta = content
            else:
                from app.services.help import HELP_TOPICS as _HELP_TOPICS  # noqa: PLC0415
                resposta = (
                    f"Não encontrei essa opção. Escolha um número entre 1 e {len(_HELP_TOPICS)} ou me diga o que precisa."
                )
        elif intent == "greeting":
            _nome = get_user_name(user_id)
            _saudacao = f"Olá, {_nome}!" if _nome else "Olá!"
            resposta = (
                f"{_saudacao} Por aqui estou de olho nos seus gastos. "
                "Pode registrar uma despesa, me perguntar quanto gastou, "
                "ver sua saúde financeira ou enviar um extrato ou fatura."
            )
        elif intent == "card_setup_request":
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = (
                "Claro. Vamos cadastrar seus cartões e deixar isso redondo.\n\n"
                f"{card_count_prompt()}"
            )
        elif intent == "recent_transactions":
            resposta = listar_ultimas_transacoes(user_id)
        elif intent == "budget_status":
            categoria = extract_budget_category(mensagem)
            if categoria:
                resposta = build_budget_status_message(user_id, categoria)
            else:
                resposta = build_all_budgets_status_message(user_id)
        elif "quanto gastei" in msg_lower and "transporte" in msg_lower:
            resposta = resumo_categoria(user_id, "Transporte")
        elif "quanto gastei" in msg_lower and "alimentacao" in msg_lower:
            resposta = resumo_categoria(user_id, "Alimentacao")
        elif "quanto gastei" in msg_lower:
            resposta = resumo_mes(user_id)
        elif not mensagem:
            resposta = "Recebi sua mensagem, mas estava vazia. Me manda o que você quiser registrar ou perguntar."
        else:
            try:
                resposta = process_user_message(mensagem, user_id)["resposta"]
            except HTTPException as exc:
                # 422 = mensagem não reconhecida como transação — responde com contexto útil
                if exc.status_code == 422:
                    resposta = exc.detail
                else:
                    raise
    except HTTPException as exc:
        resposta = exc.detail
    except Exception:
        logger.exception("webhook_error user_id=%s intent=%s", user_id, intent)
        resposta = "Tive um problema para processar sua mensagem agora. Se puder, tente novamente em instantes."

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

    try:
        deleted = delete_user_account(payload.email)
        return {"deleted": deleted}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao limpar usuario: {exc}") from exc
