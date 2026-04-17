from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.auth import create_token, get_current_user
from app.config import get_settings
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
    build_invoice_status_message,
    complete_document_onboarding,
    document_invite_prompt,
    document_upload_prompt,
    get_incoming_media,
    has_document_type,
    is_invoice_followup_message,
    is_document_onboarding_completed,
    is_waiting_for_document,
    process_stored_document,
    register_received_document,
    should_skip_document_onboarding,
    should_start_document_onboarding,
    start_document_onboarding,
)
from app.services.financial_health import build_financial_health_message
from app.services.onboarding import (
    ACCOUNT_SNAPSHOT_PENDING,
    BUDGET_SETUP_PENDING,
    CARD_COUNT_PENDING,
    COST_REVIEW_PENDING,
    CARD_DETAILS_PENDING,
    CARD_INVOICE_PENDING,
    CARD_NAMES_PENDING,
    DOCUMENT_ONBOARDING_PENDING,
    advance_card_progress,
    build_card_setup_confirmation,
    build_cost_review_confirmation,
    ONBOARDING_COMPLETE,
    account_snapshot_prompt,
    card_count_prompt,
    card_details_prompt,
    card_invoice_prompt,
    card_names_prompt,
    cost_review_adjustment_prompt,
    cost_review_prompt,
    has_cost_review_candidates,
    get_current_card,
    get_card_names,
    get_onboarding_state,
    get_pending_card_total,
    infer_cost_candidates,
    is_confirmation_no,
    is_confirmation_yes,
    is_cost_review_completed,
    parse_card_count,
    parse_card_names_llm_first,
    parse_card_details_message,
    parse_balance_message,
    parse_cost_review_adjustments,
    save_current_card_details,
    save_card_count,
    save_cost_candidates,
    save_card_names,
    save_current_balance,
    set_pending_card_index,
    set_onboarding_state,
    should_skip_account_snapshot,
    should_skip_card_setup,
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
            "Ola! Que bom ter voce por aqui.\n\n"
            "Eu sou o Navi e vou te ajudar a acompanhar seus gastos de um jeito leve, sem complicacao.\n\n"
            f"{account_snapshot_prompt()}"
        )
        return Response(content=build_twiml(resposta), media_type="application/xml")

    def _register_document_upload(forced_type: str | None = None) -> str:
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
        )
        return resposta

    msg_lower = normalize_text(mensagem)
    intent = detect_intent(mensagem)
    if intent == "transaction" and is_invoice_followup_message(user_id, mensagem):
        intent = "invoice_status"
    onboarding_state = get_onboarding_state(user_id)
    existing_card_names = get_card_names(user_id)

    if onboarding_state == ACCOUNT_SNAPSHOT_PENDING:
        if incoming_media:
            _register_document_upload("extrato")
            set_onboarding_state(user_id, BUDGET_SETUP_PENDING)
            resposta = (
                "Recebi seu extrato e ja deixei esse arquivo salvo aqui na sua base financeira.\n\n"
                "Com ele, eu consigo montar seu ponto de partida e usar essas informacoes nas proximas analises.\n\n"
                f"{onboarding_budget_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        balance = parse_balance_message(mensagem)
        if balance is not None:
            save_current_balance(user_id, balance)
            set_onboarding_state(user_id, BUDGET_SETUP_PENDING)
            resposta = (
                f"Perfeito. Anotei seu saldo atual em R${balance:.2f}.\n\n"
                "Isso ja me da um bom ponto de partida para te orientar melhor.\n\n"
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
                "Se for mais facil, voce tambem pode simplesmente me dizer o saldo atual da sua conta."
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        if intent == "card_setup_request":
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = (
                "Claro. Vamos organizar seus cartoes por aqui tambem.\n\n"
                f"{card_count_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        resposta = account_snapshot_prompt()
        return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == BUDGET_SETUP_PENDING or not is_budget_onboarding_completed(user_id):
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
                    "Claro, podemos comecar por esse documento.\n\n"
                    f"{document_upload_prompt(user_id)}\n\n"
                    'Se em algum momento quiser voltar aos limites, me mande algo como "Farmacia 290, mercado 1200".'
                )
                return Response(content=build_twiml(resposta), media_type="application/xml")
            except HTTPException as exc:
                return Response(content=build_twiml(exc.detail), media_type="application/xml")
            except Exception:
                resposta = (
                    "Recebi seu documento, mas tive um problema para processa-lo agora. "
                    "Se puder, tente de novo daqui a pouco."
                )
                return Response(content=build_twiml(resposta), media_type="application/xml")
        if incoming_media:
            _register_document_upload()
            resposta = (
                "Recebi seu documento e ja deixei isso guardado aqui.\n\n"
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
                "Claro. Vamos organizar seus cartoes antes de seguir.\n\n"
                f"{card_count_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        budgets = parse_budget_message(mensagem)
        if budgets:
            next_state = COST_REVIEW_PENDING if has_cost_review_candidates(user_id) and not is_cost_review_completed(user_id) else CARD_COUNT_PENDING
            save_budgets(user_id, budgets, next_state)
            if next_state == COST_REVIEW_PENDING:
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
            return Response(content=build_twiml(resposta), media_type="application/xml")
        if should_skip_budget_onboarding(mensagem):
            next_state = COST_REVIEW_PENDING if has_cost_review_candidates(user_id) and not is_cost_review_completed(user_id) else CARD_COUNT_PENDING
            mark_budget_onboarding_completed(user_id, next_state)
            if next_state == COST_REVIEW_PENDING:
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
        resposta = (
            "Ainda nao consegui anotar seus limites mensais.\n\n"
            f"{onboarding_budget_prompt()}"
        )
        return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == COST_REVIEW_PENDING:
        fixed_costs, variable_costs = infer_cost_candidates(user_id)
        if not fixed_costs and not variable_costs:
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = card_count_prompt()
            return Response(content=build_twiml(resposta), media_type="application/xml")

        if should_skip_budget_onboarding(mensagem):
            save_cost_candidates(user_id, confirmed=False)
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
            )
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = (
                f"{build_cost_review_confirmation(adjusted_fixed, adjusted_variable)}\n\n"
                f"{card_count_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        if is_confirmation_yes(mensagem):
            save_cost_candidates(user_id, confirmed=True)
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = (
                "Perfeito. Vou considerar essa leitura inicial dos seus custos para te acompanhar melhor.\n\n"
                f"{card_count_prompt()}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

        if is_confirmation_no(mensagem):
            resposta = cost_review_adjustment_prompt()
            return Response(content=build_twiml(resposta), media_type="application/xml")

        resposta = cost_review_prompt(fixed_costs, variable_costs)
        return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == CARD_COUNT_PENDING:
        try:
            if should_skip_card_setup(mensagem):
                save_card_count(user_id, 0)
                if has_document_type(user_id, "extrato"):
                    set_onboarding_state(user_id, ONBOARDING_COMPLETE)
                    resposta = (
                        "Tudo bem. Como eu ja tenho seu extrato, isso ja me da uma boa base inicial para te acompanhar.\n\n"
                        "Se depois voce quiser cadastrar algum cartao, eu organizo isso com voce."
                    )
                else:
                    set_onboarding_state(user_id, DOCUMENT_ONBOARDING_PENDING)
                    resposta = (
                        "Tudo bem. A gente pode cadastrar seus cartoes depois.\n\n"
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
                            "Perfeito. Entendi que voce nao quer acompanhar cartoes por agora.\n\n"
                            "Como eu ja tenho seu extrato, ja consigo seguir com uma boa base inicial."
                        )
                    else:
                        set_onboarding_state(user_id, DOCUMENT_ONBOARDING_PENDING)
                        resposta = (
                            "Perfeito. Entendi que voce nao quer acompanhar cartoes por agora.\n\n"
                            f"{document_invite_prompt(user_id)}"
                        )
                    return Response(content=build_twiml(resposta), media_type="application/xml")

                set_onboarding_state(user_id, CARD_NAMES_PENDING)
                resposta = card_names_prompt(card_total)
                return Response(content=build_twiml(resposta), media_type="application/xml")

            if intent == "document_request":
                resposta = (
                    "Consigo sim. Antes, so me conta quantos cartoes voce quer acompanhar comigo, que eu organizo isso certinho.\n\n"
                    f"{card_count_prompt()}"
                )
                return Response(content=build_twiml(resposta), media_type="application/xml")

            resposta = card_count_prompt()
            return Response(content=build_twiml(resposta), media_type="application/xml")
        except HTTPException as exc:
            return Response(content=build_twiml(exc.detail), media_type="application/xml")
        except Exception:
            resposta = (
                "Tive um problema para anotar essa etapa dos cartoes agora. "
                "Se puder, tente me responder novamente com a quantidade de cartoes."
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == CARD_NAMES_PENDING:
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
                        "Tudo bem. A gente pode deixar os cartoes para depois.\n\n"
                        "Como eu ja tenho seu extrato, isso ja me ajuda bastante por enquanto."
                    )
                else:
                    set_onboarding_state(user_id, DOCUMENT_ONBOARDING_PENDING)
                    resposta = (
                        "Tudo bem. A gente pode deixar os cartoes para depois.\n\n"
                        f"{document_invite_prompt(user_id)}"
                    )
                return Response(content=build_twiml(resposta), media_type="application/xml")

            card_names = parse_card_names_llm_first(mensagem, expected_count)
            if card_names:
                save_card_names(user_id, card_names)
                set_pending_card_index(user_id, 0)
                set_onboarding_state(user_id, CARD_DETAILS_PENDING)
                confirmed_names = get_card_names(user_id) or card_names
                resposta = (
                    f"{build_card_setup_confirmation(confirmed_names)}\n\n"
                    f"{card_details_prompt(confirmed_names[0])}"
                )
                return Response(content=build_twiml(resposta), media_type="application/xml")

            resposta = (
                "Quero deixar os nomes dos seus cartoes do jeito que faca sentido para voce.\n\n"
                f"{card_names_prompt(expected_count)}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        except HTTPException as exc:
            return Response(content=build_twiml(exc.detail), media_type="application/xml")
        except Exception:
            resposta = (
                "Tive um problema para salvar os nomes dos seus cartoes agora. "
                "Se puder, tente me mandar os nomes novamente separados por virgula."
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == CARD_DETAILS_PENDING:
        try:
            current_card = get_current_card(user_id)
            if not current_card:
                set_pending_card_index(user_id, 0)
                set_onboarding_state(user_id, CARD_INVOICE_PENDING)
                resposta = document_upload_prompt(user_id)
                return Response(content=build_twiml(resposta), media_type="application/xml")

            if should_skip_card_setup(mensagem):
                next_card = advance_card_progress(user_id)
                if next_card:
                    resposta = (
                        f"Tudo bem. A gente pode preencher esses detalhes do {current_card['nome_cartao']} depois.\n\n"
                        f"{card_details_prompt(str(next_card['nome_cartao']))}"
                    )
                else:
                    set_pending_card_index(user_id, 0)
                    set_onboarding_state(user_id, CARD_INVOICE_PENDING)
                    resposta = (
                        "Tudo bem. Se precisar, a gente completa esses detalhes de compra mais tarde.\n\n"
                        f"{document_upload_prompt(user_id)}"
                    )
                return Response(content=build_twiml(resposta), media_type="application/xml")

            best_day, limit_value = parse_card_details_message(mensagem)
            if best_day is not None or limit_value is not None:
                updated_card = save_current_card_details(user_id, best_day, limit_value) or current_card
                next_card = advance_card_progress(user_id)
                detail_parts = []
                if updated_card.get("dia_melhor_compra"):
                    detail_parts.append(f"melhor dia {updated_card['dia_melhor_compra']}")
                if updated_card.get("limite_credito") is not None:
                    detail_parts.append(f"limite de R${float(updated_card['limite_credito']):.2f}")
                detail_summary = ", ".join(detail_parts) if detail_parts else "essas informacoes"

                if next_card:
                    resposta = (
                        f"Perfeito. Ja anotei {detail_summary} para o {updated_card['nome_cartao']}.\n\n"
                        f"{card_details_prompt(str(next_card['nome_cartao']))}"
                    )
                else:
                    set_pending_card_index(user_id, 0)
                    set_onboarding_state(user_id, CARD_INVOICE_PENDING)
                    resposta = (
                        f"Perfeito. Ja anotei {detail_summary} para o {updated_card['nome_cartao']}.\n\n"
                        f"{document_upload_prompt(user_id)}"
                    )
                return Response(content=build_twiml(resposta), media_type="application/xml")

            resposta = (
                f"Quero deixar o {current_card['nome_cartao']} bem configurado desde o inicio.\n\n"
                f"{card_details_prompt(str(current_card['nome_cartao']))}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        except HTTPException as exc:
            return Response(content=build_twiml(exc.detail), media_type="application/xml")
        except Exception:
            current_card = get_current_card(user_id)
            fallback_name = current_card["nome_cartao"] if current_card else "esse cartao"
            resposta = (
                f"Tive um problema para salvar os detalhes do {fallback_name} agora.\n\n"
                f"{card_details_prompt(str(fallback_name))}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == CARD_INVOICE_PENDING:
        try:
            current_card = get_current_card(user_id)
            if not current_card:
                complete_document_onboarding(user_id)
                resposta = "Perfeito. Ja organizei essa etapa inicial e agora posso seguir com voce normalmente."
                return Response(content=build_twiml(resposta), media_type="application/xml")

            if incoming_media:
                _register_document_upload("fatura_cartao")
                next_card = advance_card_progress(user_id)
                if next_card:
                    resposta = (
                        f"Perfeito. Ja deixei a fatura do {current_card['nome_cartao']} salva por aqui.\n\n"
                        "Isso ja me ajuda a acompanhar melhor esse cartao e a deixar sua base financeira mais redonda.\n\n"
                        f"Agora me manda a fatura atual do {next_card['nome_cartao']}."
                    )
                else:
                    complete_document_onboarding(user_id)
                    resposta = (
                        f"Perfeito. Ja deixei a fatura do {current_card['nome_cartao']} salva por aqui.\n\n"
                        "Com isso, terminei de organizar sua base inicial e agora ja consigo te acompanhar de um jeito bem mais completo daqui para frente."
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

            resposta = card_invoice_prompt(str(current_card["nome_cartao"]))
            return Response(content=build_twiml(resposta), media_type="application/xml")
        except HTTPException as exc:
            return Response(content=build_twiml(exc.detail), media_type="application/xml")
        except Exception:
            current_card = get_current_card(user_id)
            fallback_name = current_card["nome_cartao"] if current_card else "esse cartao"
            resposta = (
                f"Tive um problema para seguir com a fatura do {fallback_name} agora.\n\n"
                f"{card_invoice_prompt(str(fallback_name))}"
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state != ONBOARDING_COMPLETE and not is_document_onboarding_completed(user_id):
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
                "Depois que voce me mandar os novos valores, eu atualizo tudo por aqui."
            )
            return Response(content=build_twiml(resposta), media_type="application/xml")
        if intent == "card_setup_request":
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = (
                "Perfeito. Vamos trazer seus cartoes para dentro dessa organizacao.\n\n"
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
                    "Recebi seu documento, mas tive um problema para processa-lo agora. "
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
                resposta = document_upload_prompt(user_id)
                return Response(content=build_twiml(resposta), media_type="application/xml")
            except HTTPException as exc:
                return Response(content=build_twiml(exc.detail), media_type="application/xml")
            except Exception:
                resposta = (
                    "Recebi seu documento, mas tive um problema para processa-lo agora. "
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
        resposta = document_invite_prompt(user_id)
        return Response(content=build_twiml(resposta), media_type="application/xml")

    if onboarding_state == ONBOARDING_COMPLETE and has_document_type(user_id, "extrato") and not existing_card_names:
        card_total = parse_card_count(mensagem)
        if card_total is not None:
            try:
                save_card_count(user_id, card_total)
                if card_total <= 0:
                    resposta = (
                        "Perfeito. Entendi que voce nao quer acompanhar cartoes por agora.\n\n"
                        "Se depois mudar de ideia, eu organizo isso com voce."
                    )
                else:
                    set_onboarding_state(user_id, CARD_NAMES_PENDING)
                    resposta = card_names_prompt(card_total)
                return Response(content=build_twiml(resposta), media_type="application/xml")
            except Exception:
                resposta = (
                    "Entendi que voce quer cadastrar cartoes, mas tive um problema para salvar essa etapa agora.\n\n"
                    f"{card_count_prompt()}"
                )
                return Response(content=build_twiml(resposta), media_type="application/xml")

    try:
        if incoming_media:
            resposta = _register_document_upload()
            resposta = (
                f"{resposta}\n\n"
                "Se quiser me ajudar a interpretar melhor, voce tambem pode escrever se isso e um extrato ou uma fatura."
            )
        elif intent == "confirm_yes":
            resposta = confirm_pending_transaction(user_id)["resposta"]
        elif intent == "confirm_no":
            resposta = reject_pending_transaction(user_id)
        elif intent == "document_request":
            start_document_onboarding(user_id)
            resposta = (
                "Claro. Posso te ajudar com esse documento.\n\n"
                f"{document_upload_prompt(user_id)}"
            )
        elif intent == "invoice_status":
            resposta = build_invoice_status_message(user_id, mensagem)
        elif intent == "financial_health":
            resposta = build_financial_health_message(user_id)
        elif intent == "card_setup_request":
            set_onboarding_state(user_id, CARD_COUNT_PENDING)
            resposta = (
                "Claro. Vamos cadastrar seus cartoes e deixar isso redondo.\n\n"
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
