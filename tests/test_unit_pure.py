"""
Tests – Pure Unit Tests (no DB, no network)
Covers: formatting, intent detection, balance parsing, budget parsing,
        card count parsing, confirmation logic, text normalization,
        and security-relevant pure functions.

These tests run without a database and without the 'client' fixture.
"""

import pytest
from decimal import Decimal


# ──────────────────────────────────────────────────────────────────
# Formatting
# ──────────────────────────────────────────────────────────────────

class TestFormatBRL:
    def test_formats_integer(self):
        from app.services.formatting import format_brl
        assert format_brl(1000) == "R$1.000,00"

    def test_formats_float(self):
        from app.services.formatting import format_brl
        assert format_brl(1234.56) == "R$1.234,56"

    def test_formats_zero(self):
        from app.services.formatting import format_brl
        assert format_brl(0) == "R$0,00"

    def test_formats_none_as_zero(self):
        from app.services.formatting import format_brl
        assert format_brl(None) == "R$0,00"

    def test_formats_small_value(self):
        from app.services.formatting import format_brl
        assert format_brl(0.50) == "R$0,50"

    def test_formats_large_value(self):
        from app.services.formatting import format_brl
        result = format_brl(1000000)
        assert "1.000.000" in result


# ──────────────────────────────────────────────────────────────────
# Intent Detection
# ──────────────────────────────────────────────────────────────────

class TestDetectIntent:
    def test_confirm_yes_sim(self):
        from app.services.conversation import detect_intent
        assert detect_intent("sim") == "confirm_yes"

    def test_confirm_yes_ok(self):
        from app.services.conversation import detect_intent
        assert detect_intent("ok") == "confirm_yes"

    def test_confirm_no_nao(self):
        from app.services.conversation import detect_intent
        assert detect_intent("nao") == "confirm_no"

    def test_recent_transactions(self):
        from app.services.conversation import detect_intent
        assert detect_intent("Ultimos gastos") == "recent_transactions"

    def test_budget_status(self):
        from app.services.conversation import detect_intent
        assert detect_intent("Quanto ainda posso gastar") == "budget_status"

    def test_invoice_status(self):
        from app.services.conversation import detect_intent
        assert detect_intent("Qual o valor da minha fatura") == "invoice_status"

    def test_financial_health(self):
        from app.services.conversation import detect_intent
        assert detect_intent("Como esta minha saude financeira") == "financial_health"

    def test_card_setup_request(self):
        from app.services.conversation import detect_intent
        assert detect_intent("Quero cadastrar meu cartao") == "card_setup_request"

    def test_document_request_fatura(self):
        from app.services.conversation import detect_intent
        assert detect_intent("Quero enviar a fatura") == "document_request"

    def test_transaction_default(self):
        from app.services.conversation import detect_intent
        assert detect_intent("Gastei R$50 no Uber") == "transaction"

    def test_case_insensitive_sim(self):
        from app.services.conversation import detect_intent
        assert detect_intent("SIM") == "confirm_yes"

    def test_accented_financial_health(self):
        from app.services.conversation import detect_intent
        assert detect_intent("Como está minha saúde financeira") == "financial_health"


# ──────────────────────────────────────────────────────────────────
# Balance Parsing
# ──────────────────────────────────────────────────────────────────

class TestParseBalance:
    def test_simple_amount(self):
        from app.services.onboarding import parse_balance_message
        assert parse_balance_message("2500") == 2500.0

    def test_amount_with_brl_prefix(self):
        from app.services.onboarding import parse_balance_message
        assert parse_balance_message("R$1.500,00") == 1500.0

    def test_amount_with_saldo_keyword(self):
        from app.services.onboarding import parse_balance_message
        result = parse_balance_message("Meu saldo é 3200")
        assert result == 3200.0

    def test_negative_amount_rejected(self):
        from app.services.onboarding import parse_balance_message
        # Negative balance should return None (function returns None for < 0)
        assert parse_balance_message("-500") is None

    def test_message_with_non_balance_keyword_rejected(self):
        from app.services.onboarding import parse_balance_message
        # "gastei" is a NON_BALANCE_KEYWORD, so should return None
        assert parse_balance_message("gastei 500") is None

    def test_multiple_amounts_rejected(self):
        from app.services.onboarding import parse_balance_message
        # Two amounts → ambiguous → None
        assert parse_balance_message("500 e 300") is None


# ──────────────────────────────────────────────────────────────────
# Budget Parsing
# ──────────────────────────────────────────────────────────────────

class TestParseBudget:
    def test_single_category(self):
        from app.services.budgets import parse_budget_message
        result = parse_budget_message("farmacia 300")
        assert "farmacia" in result
        assert result["farmacia"] == Decimal("300")

    def test_multiple_categories(self):
        from app.services.budgets import parse_budget_message
        result = parse_budget_message("farmacia 300, mercado 1500, lazer 800")
        assert len(result) == 3
        assert result["farmacia"] == Decimal("300")
        assert result["mercado"] == Decimal("1500")
        assert result["lazer"] == Decimal("800")

    def test_supermercado_maps_to_mercado(self):
        from app.services.budgets import parse_budget_message
        result = parse_budget_message("supermercado 1200")
        assert "mercado" in result

    def test_amount_with_comma(self):
        from app.services.budgets import parse_budget_message
        result = parse_budget_message("farmacia 1.200,50")
        assert "farmacia" in result
        assert result["farmacia"] == Decimal("1200.50")

    def test_empty_message_returns_empty(self):
        from app.services.budgets import parse_budget_message
        assert parse_budget_message("hello") == {}

    def test_skip_word_is_not_budget(self):
        from app.services.budgets import parse_budget_message
        assert parse_budget_message("PULAR") == {}


# ──────────────────────────────────────────────────────────────────
# Card Count Parsing
# ──────────────────────────────────────────────────────────────────

class TestParseCardCount:
    def test_digit_one(self):
        from app.services.onboarding import parse_card_count
        assert parse_card_count("1") == 1

    def test_digit_three(self):
        from app.services.onboarding import parse_card_count
        assert parse_card_count("3") == 3

    def test_word_dois(self):
        from app.services.onboarding import parse_card_count
        assert parse_card_count("dois") == 2

    def test_word_zero_nenhum(self):
        from app.services.onboarding import parse_card_count
        assert parse_card_count("nenhum") == 0

    def test_zero_digit(self):
        from app.services.onboarding import parse_card_count
        assert parse_card_count("0") == 0

    def test_empty_string_returns_none(self):
        from app.services.onboarding import parse_card_count
        assert parse_card_count("") is None

    def test_garbage_returns_none(self):
        from app.services.onboarding import parse_card_count
        assert parse_card_count("banana") is None


# ──────────────────────────────────────────────────────────────────
# Card Details Parsing
# ──────────────────────────────────────────────────────────────────

class TestParseCardDetails:
    def test_parses_day_and_limit(self):
        from app.services.onboarding import parse_card_details_message
        day, limit = parse_card_details_message("melhor dia 20 e limite 5000")
        assert day == 20
        assert limit == 5000.0

    def test_parses_only_limit(self):
        from app.services.onboarding import parse_card_details_message
        day, limit = parse_card_details_message("limite 3000")
        assert limit == 3000.0

    def test_parses_k_suffix(self):
        from app.services.onboarding import parse_card_details_message
        day, limit = parse_card_details_message("limite 5k")
        assert limit == 5000.0

    def test_returns_none_for_empty(self):
        from app.services.onboarding import parse_card_details_message
        day, limit = parse_card_details_message("")
        assert day is None
        assert limit is None


# ──────────────────────────────────────────────────────────────────
# Confirmation Logic
# ──────────────────────────────────────────────────────────────────

class TestConfirmationWords:
    def test_sim_is_yes(self):
        from app.services.onboarding import is_confirmation_yes
        assert is_confirmation_yes("sim") is True

    def test_perfeito_is_yes(self):
        from app.services.onboarding import is_confirmation_yes
        assert is_confirmation_yes("perfeito") is True

    def test_nao_is_no(self):
        from app.services.onboarding import is_confirmation_no
        assert is_confirmation_no("nao") is True

    def test_random_word_is_not_yes(self):
        from app.services.onboarding import is_confirmation_yes
        assert is_confirmation_yes("talvez") is False

    def test_random_word_is_not_no(self):
        from app.services.onboarding import is_confirmation_no
        assert is_confirmation_no("continue") is False


# ──────────────────────────────────────────────────────────────────
# Skip Detection
# ──────────────────────────────────────────────────────────────────

class TestSkipWords:
    def test_pular_skips_budget(self):
        from app.services.budgets import should_skip_budget_onboarding
        assert should_skip_budget_onboarding("PULAR") is True

    def test_depois_skips_budget(self):
        from app.services.budgets import should_skip_budget_onboarding
        assert should_skip_budget_onboarding("depois") is True

    def test_regular_text_does_not_skip(self):
        from app.services.budgets import should_skip_budget_onboarding
        assert should_skip_budget_onboarding("farmacia 300") is False

    def test_pular_skips_snapshot(self):
        from app.services.onboarding import should_skip_account_snapshot
        assert should_skip_account_snapshot("pular") is True

    def test_pular_skips_card(self):
        from app.services.onboarding import should_skip_card_setup
        assert should_skip_card_setup("pular") is True


# ──────────────────────────────────────────────────────────────────
# Budget Edit Detection
# ──────────────────────────────────────────────────────────────────

class TestBudgetEditDetection:
    def test_edit_request_detected(self):
        from app.services.budgets import is_budget_edit_request
        assert is_budget_edit_request("quero alterar meu orcamento") is True

    def test_no_context_word_not_edit(self):
        from app.services.budgets import is_budget_edit_request
        assert is_budget_edit_request("quero alterar") is False

    def test_no_edit_word_not_edit(self):
        from app.services.budgets import is_budget_edit_request
        assert is_budget_edit_request("meu limite") is False


# ──────────────────────────────────────────────────────────────────
# Confirmation Request Logic (should_request_confirmation)
# ──────────────────────────────────────────────────────────────────

class TestShouldRequestConfirmation:
    """
    should_request_confirmation returns True when confirmation is needed.
    Returns False (no confirmation needed) only when user provides BOTH
    an explicit amount (R$XX or XX,XX) AND a transaction verb.
    """

    def test_explicit_transaction_no_confirmation_needed(self):
        from app.services.conversation import should_request_confirmation
        # Clear verb + R$ amount → no confirmation needed
        assert should_request_confirmation("Gastei R$50 no Uber") is False

    def test_ambiguous_message_needs_confirmation(self):
        from app.services.conversation import should_request_confirmation
        # No R$ prefix, no verb → confirmation needed
        assert should_request_confirmation("cinema 75") is True

    def test_verb_no_amount_needs_confirmation(self):
        from app.services.conversation import should_request_confirmation
        # Has verb but no R$ prefix and no comma-decimal → confirmation
        assert should_request_confirmation("gastei 50 no uber") is True

    def test_amount_no_verb_needs_confirmation(self):
        from app.services.conversation import should_request_confirmation
        # Has R$ but no transaction verb → confirmation
        assert should_request_confirmation("R$50") is True


# ──────────────────────────────────────────────────────────────────
# Category Classification (chat.py)
# ──────────────────────────────────────────────────────────────────

class TestClassificarCategoria:
    def test_uber_maps_to_transporte(self):
        from app.services.chat import classificar_categoria
        assert classificar_categoria("uber para o trabalho") == "transporte"

    def test_ifood_maps_to_alimentacao(self):
        from app.services.chat import classificar_categoria
        assert classificar_categoria("ifood pizza") == "alimentacao"

    def test_netflix_maps_to_lazer(self):
        from app.services.chat import classificar_categoria
        assert classificar_categoria("assinatura netflix") == "lazer"

    def test_aluguel_maps_to_moradia(self):
        from app.services.chat import classificar_categoria
        assert classificar_categoria("aluguel do apartamento") == "moradia"

    def test_unknown_returns_none(self):
        from app.services.chat import classificar_categoria
        assert classificar_categoria("produto generico desconhecido") is None


# ──────────────────────────────────────────────────────────────────
# Auth – token creation and verification
# ──────────────────────────────────────────────────────────────────

class TestAuthFunctions:
    def test_hash_and_verify_password(self):
        from app.auth import hash_password, verify_password
        hashed = hash_password("mypassword")
        assert verify_password("mypassword", hashed) is True
        assert verify_password("wrong", hashed) is False

    def test_create_token_returns_string(self):
        from app.auth import create_token
        token = create_token(42)
        assert isinstance(token, str)
        assert len(token) > 10

    def test_token_decodes_correctly(self):
        from app.auth import create_token
        from app.config import get_settings
        from jose import jwt
        settings = get_settings()
        token = create_token(99)
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        assert payload["user_id"] == 99

    def test_no_expiry_in_token(self):
        """SECURITY: Tokens have no 'exp' claim – documents known finding."""
        from app.auth import create_token
        from app.config import get_settings
        from jose import jwt
        settings = get_settings()
        token = create_token(1)
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        assert "exp" not in payload, "FINDING: Token has no expiry claim"
