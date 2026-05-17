"""
tests/test_invoice_analysis_response.py — Issue #157
=====================================================
Verifies that process_stored_document always sends a WhatsApp response and
advances the onboarding state, even when PDF extraction fails.

Root cause: the else-branch of process_stored_document only logged a warning,
leaving the user stuck in CARD_INVOICE_PENDING with no reply.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from app.services.documents import _notify_analysis_failure, process_stored_document
from app.services.onboarding import CARD_COUNT_PENDING, INVOICE_REVIEW_PENDING


# ---------------------------------------------------------------------------
# _notify_analysis_failure unit tests
# ---------------------------------------------------------------------------


class TestNotifyAnalysisFailure:
    """_notify_analysis_failure sends fallback message and advances state."""

    def test_fatura_sends_fallback_message(self) -> None:
        """AC: When fatura analysis fails, a user-friendly message is sent via Twilio."""
        mock_split = MagicMock()
        mock_set_state = MagicMock()
        with (
            patch("app.services.twilio.responder_split", mock_split),
            patch("app.services.onboarding.set_onboarding_state", mock_set_state),
        ):
            _notify_analysis_failure(
                document_id=1,
                user_id=42,
                numero="whatsapp:+5511900000000",
                on_complete_state=INVOICE_REVIEW_PENDING,
                hinted_type="fatura_cartao",
            )

        mock_split.assert_called_once()
        sent_msg: str = mock_split.call_args[0][1]
        assert "fatura" in sent_msg.lower()
        assert "automaticamente" in sent_msg.lower()

    def test_extrato_sends_fallback_message(self) -> None:
        """AC: When extrato analysis fails, a user-friendly message is sent."""
        mock_split = MagicMock()
        mock_set_state = MagicMock()
        with (
            patch("app.services.twilio.responder_split", mock_split),
            patch("app.services.onboarding.set_onboarding_state", mock_set_state),
        ):
            _notify_analysis_failure(
                document_id=2,
                user_id=42,
                numero="whatsapp:+5511900000000",
                on_complete_state=None,
                hinted_type="extrato",
            )

        mock_split.assert_called_once()

    def test_state_is_advanced_on_failure(self) -> None:
        """AC: on_complete_state is set even when analysis fails, so user does not stall."""
        mock_split = MagicMock()
        mock_set_state = MagicMock()
        with (
            patch("app.services.twilio.responder_split", mock_split),
            patch("app.services.onboarding.set_onboarding_state", mock_set_state),
        ):
            _notify_analysis_failure(
                document_id=3,
                user_id=42,
                numero="whatsapp:+5511900000000",
                on_complete_state=INVOICE_REVIEW_PENDING,
                hinted_type="fatura_cartao",
            )

        mock_set_state.assert_called_once_with(42, INVOICE_REVIEW_PENDING)

    def test_no_state_set_when_none(self) -> None:
        """AC: When on_complete_state is None, set_onboarding_state is not called."""
        mock_split = MagicMock()
        mock_set_state = MagicMock()
        with (
            patch("app.services.twilio.responder_split", mock_split),
            patch("app.services.onboarding.set_onboarding_state", mock_set_state),
        ):
            _notify_analysis_failure(
                document_id=4,
                user_id=42,
                numero="whatsapp:+5511900000000",
                on_complete_state=None,
                hinted_type="fatura_cartao",
            )

        mock_set_state.assert_not_called()

    def test_twilio_error_is_swallowed_and_logged(self) -> None:
        """AC: If Twilio send raises, the exception is caught — state was still advanced."""
        mock_split = MagicMock(side_effect=RuntimeError("Twilio unavailable"))
        mock_set_state = MagicMock()
        with (
            patch("app.services.twilio.responder_split", mock_split),
            patch("app.services.onboarding.set_onboarding_state", mock_set_state),
        ):
            # Must not propagate
            _notify_analysis_failure(
                document_id=5,
                user_id=42,
                numero="whatsapp:+5511900000000",
                on_complete_state=INVOICE_REVIEW_PENDING,
                hinted_type="fatura_cartao",
            )

        mock_set_state.assert_called_once_with(42, INVOICE_REVIEW_PENDING)


# ---------------------------------------------------------------------------
# process_stored_document integration-level unit tests
# ---------------------------------------------------------------------------


class TestProcessStoredDocumentFailurePath:
    """process_stored_document calls _notify_analysis_failure when analysis is None."""

    def _make_settings(self) -> MagicMock:
        s = MagicMock()
        s.account_sid = "sid"
        s.auth_token = "token"
        s.openai_api_key = "sk-test"
        return s

    @patch("app.services.documents.get_user_locale", return_value="pt-BR")
    @patch("app.services.documents._update_document_analysis")
    @patch("app.services.documents._download_media_bytes", side_effect=RuntimeError("network error"))
    @patch("app.services.documents._notify_analysis_failure")
    def test_download_failure_triggers_fallback(
        self,
        mock_notify: MagicMock,
        mock_download: MagicMock,
        mock_update: MagicMock,
        mock_locale: MagicMock,
    ) -> None:
        """AC: If media download raises, _notify_analysis_failure is called with the numero."""
        process_stored_document(
            document_id=10,
            user_id=99,
            media_url="https://api.twilio.com/media/1",
            media_content_type="application/pdf",
            message_text="",
            hinted_type="fatura_cartao",
            card_id=None,
            on_complete_numero="whatsapp:+5511900000000",
            on_complete_state=INVOICE_REVIEW_PENDING,
        )

        mock_notify.assert_called_once_with(
            10, 99, "whatsapp:+5511900000000", INVOICE_REVIEW_PENDING, "fatura_cartao"
        )

    @patch("app.services.documents.get_user_locale", return_value="pt-BR")
    @patch("app.services.documents._update_document_analysis")
    @patch("app.services.documents._download_media_bytes", return_value=b"%PDF")
    @patch("app.services.documents._extract_document_analysis", return_value=None)
    @patch("app.services.documents._notify_analysis_failure")
    def test_empty_openai_response_triggers_fallback(
        self,
        mock_notify: MagicMock,
        mock_extract: MagicMock,
        mock_download: MagicMock,
        mock_update: MagicMock,
        mock_locale: MagicMock,
    ) -> None:
        """AC: If OpenAI returns None (empty/unsupported), _notify_analysis_failure is called."""
        process_stored_document(
            document_id=11,
            user_id=99,
            media_url="https://api.twilio.com/media/2",
            media_content_type="application/pdf",
            message_text="",
            hinted_type="fatura_cartao",
            card_id=None,
            on_complete_numero="whatsapp:+5511900000000",
            on_complete_state=INVOICE_REVIEW_PENDING,
        )

        mock_notify.assert_called_once_with(
            11, 99, "whatsapp:+5511900000000", INVOICE_REVIEW_PENDING, "fatura_cartao"
        )

    @patch("app.services.documents.get_user_locale", return_value="pt-BR")
    @patch("app.services.documents._update_document_analysis")
    @patch("app.services.documents._download_media_bytes", return_value=b"%PDF")
    @patch("app.services.documents._extract_document_analysis", return_value=None)
    @patch("app.services.documents._notify_analysis_failure")
    def test_no_fallback_when_no_numero(
        self,
        mock_notify: MagicMock,
        mock_extract: MagicMock,
        mock_download: MagicMock,
        mock_update: MagicMock,
        mock_locale: MagicMock,
    ) -> None:
        """AC: When on_complete_numero is None, _notify_analysis_failure is not called."""
        process_stored_document(
            document_id=12,
            user_id=99,
            media_url="https://api.twilio.com/media/3",
            media_content_type="application/pdf",
            message_text="",
            hinted_type="fatura_cartao",
            card_id=None,
            on_complete_numero=None,
            on_complete_state=None,
        )

        mock_notify.assert_not_called()

    @patch("app.services.documents.get_user_locale", return_value="pt-BR")
    @patch("app.services.documents._update_document_analysis")
    @patch("app.services.documents._download_media_bytes", return_value=b"%PDF")
    @patch("app.services.documents._extract_document_analysis")
    @patch("app.services.documents._persist_financial_context")
    @patch("app.services.documents._notify_analysis_failure")
    def test_success_path_does_not_call_fallback(
        self,
        mock_notify: MagicMock,
        mock_persist: MagicMock,
        mock_extract: MagicMock,
        mock_download: MagicMock,
        mock_update: MagicMock,
        mock_locale: MagicMock,
    ) -> None:
        """AC: When analysis succeeds, _notify_analysis_failure is NOT called."""
        mock_extract.return_value = {"document_type": "fatura_cartao", "statement_rows": []}
        mock_responder = MagicMock()
        mock_set_state = MagicMock()
        with (
            patch("app.services.onboarding.set_onboarding_state", mock_set_state),
            patch("app.services.twilio.responder_split", mock_responder),
        ):
            process_stored_document(
                document_id=13,
                user_id=99,
                media_url="https://api.twilio.com/media/4",
                media_content_type="application/pdf",
                message_text="",
                hinted_type="fatura_cartao",
                card_id=None,
                on_complete_numero="whatsapp:+5511900000000",
                on_complete_state=INVOICE_REVIEW_PENDING,
            )

        mock_notify.assert_not_called()
