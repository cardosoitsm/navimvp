"""
<<<<<<< HEAD
tests/stubs/twilio_sim.py -- Twilio Webhook Simulator
Generates correctly-signed Twilio webhook POST requests.
"""
from __future__ import annotations
import base64, hashlib, hmac
from dataclasses import dataclass, field
from typing import Any
from fastapi.testclient import TestClient

def compute_twilio_signature(auth_token: str, url: str, params: dict) -> str:
    s = url
    for key in sorted(params.keys()):
        s += key + params[key]
    mac = hmac.new(auth_token.encode('utf-8'), s.encode('utf-8'), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode('utf-8')

@dataclass
class TwilioSim:
    auth_token: str
    webhook_url: str
    sign: bool = True
    extra_params: dict = field(default_factory=dict)

    def _build_params(self, body, from_number, to_number='whatsapp:+5511999999999', **kwargs):
        params = {
            'Body': body,
            'From': f'whatsapp:{from_number}' if not from_number.startswith('whatsapp:') else from_number,
            'To': to_number,
            'NumMedia': '0',
            'MessageSid': 'SMstub00000000000000000000000000',
            'AccountSid': 'ACstub00000000000000000000000000',
=======
tests/stubs/twilio_sim.py — Twilio Webhook Simulator
=====================================================
Generates correctly-signed Twilio webhook POST requests so the SUT's
HMAC-SHA1 signature validation is exercised, not bypassed.

Why HMAC matters
----------------
Twilio signs every webhook with HMAC-SHA1 using the account AUTH_TOKEN
and the full request URL. If the SUT validates this signature (as it should
per SEC hardening), a test that bypasses it gives false confidence.

The TwilioSim class generates the real signature so the full validation
path is exercised. It is also useful for regression-testing the signature
validation middleware itself.

Usage
-----
    from tests.stubs.twilio_sim import TwilioSim, make_twilio_post

    # Full object API (most control)
    sim = TwilioSim(
        auth_token="test-auth-token",
        webhook_url="http://testserver/webhook",
    )
    resp = sim.send(client, body="Oi", from_number="+5511900000001")

    # Functional shortcut
    resp = make_twilio_post(
        client=client,
        auth_token="test-auth-token",
        webhook_url="http://testserver/webhook",
        body="Oi",
        from_number="+5511900000001",
    )

When to use TwilioSim vs post_webhook
--------------------------------------
- Use `post_webhook()` from `tests/harness.py` for the vast majority of
  tests. It is faster because it skips signature generation and the SUT
  does not enforce signatures in test mode.
- Use `TwilioSim` specifically when testing:
  - The signature validation middleware itself
  - The handling of requests with invalid signatures
  - The exact Twilio wire format (including MediaUrl, NumMedia, etc.)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient


# ──────────────────────────────────────────────────────────────────────────────
# HMAC signature generation (matches Twilio's algorithm exactly)
# ──────────────────────────────────────────────────────────────────────────────

def compute_twilio_signature(
    auth_token: str,
    url: str,
    params: dict[str, str],
) -> str:
    """
    Compute the X-Twilio-Signature header value.

    Algorithm (from Twilio docs):
      1. Take the full URL of the webhook (e.g. https://example.com/webhook)
      2. If it's a POST with form params, sort them alphabetically and append
         each key-value pair to the URL string.
      3. Sign the resulting string with HMAC-SHA1 using AUTH_TOKEN as the key.
      4. Base64-encode the result.

    Args:
        auth_token: The Twilio account AUTH_TOKEN.
        url:        The full webhook URL (scheme + host + path).
        params:     Form parameters as a flat string dict.

    Returns:
        Base64-encoded HMAC-SHA1 signature string.
    """
    # Step 1-2: Build the string to sign
    s = url
    for key in sorted(params.keys()):
        s += key + params[key]

    # Step 3-4: HMAC-SHA1 + base64
    mac = hmac.new(
        auth_token.encode("utf-8"),
        s.encode("utf-8"),
        hashlib.sha1,
    )
    return base64.b64encode(mac.digest()).decode("utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# TwilioSim
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class TwilioSim:
    """
    Simulates Twilio's inbound webhook POST to the /webhook endpoint.

    Generates valid HMAC-SHA1 signatures so the SUT's signature validation
    middleware is exercised with correctly-signed requests.

    Attributes:
        auth_token:   The Twilio AUTH_TOKEN (must match SUT's AUTH_TOKEN env var).
        webhook_url:  The full URL of the webhook endpoint.
                      Use "http://testserver/webhook" with FastAPI TestClient.
        sign:         If True (default), include X-Twilio-Signature header.
                      Set to False to test invalid-signature rejection.
        extra_params: Additional Twilio fields to include in every request
                      (e.g. AccountSid, NumMedia).
    """

    auth_token: str
    webhook_url: str
    sign: bool = True
    extra_params: dict[str, str] = field(default_factory=dict)

    def _build_params(
        self,
        body: str,
        from_number: str,
        to_number: str = "whatsapp:+5511999999999",
        **kwargs: str,
    ) -> dict[str, str]:
        """Build the form-encoded parameter dict as Twilio sends it."""
        params: dict[str, str] = {
            "Body": body,
            "From": f"whatsapp:{from_number}" if not from_number.startswith("whatsapp:") else from_number,
            "To": to_number,
            "NumMedia": "0",
            "MessageSid": "SMstub00000000000000000000000000",
            "AccountSid": "ACstub00000000000000000000000000",
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
        }
        params.update(self.extra_params)
        params.update(kwargs)
        return params

<<<<<<< HEAD
    def send(self, client, body, from_number, to_number='whatsapp:+5511999999999', **extra):
        params = self._build_params(body, from_number, to_number, **extra)
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        if self.sign:
            headers['X-Twilio-Signature'] = compute_twilio_signature(self.auth_token, self.webhook_url, params)
        return client.post('/webhook', data=params, headers=headers)

    def send_invalid_signature(self, client, body, from_number):
        params = self._build_params(body, from_number)
        return client.post('/webhook', data=params, headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-Twilio-Signature': 'invalid-signature-deliberately-wrong',
        })

def make_twilio_post(client, auth_token, webhook_url, body, from_number, to_number='whatsapp:+5511999999999'):
=======
    def send(
        self,
        client: TestClient,
        body: str,
        from_number: str,
        to_number: str = "whatsapp:+5511999999999",
        **extra: str,
    ) -> Any:
        """
        POST a signed webhook request to the SUT.

        Args:
            client:      FastAPI TestClient instance.
            body:        The WhatsApp message text (maps to Twilio's `Body`).
            from_number: Sender phone number, with or without "whatsapp:" prefix.
            to_number:   Recipient number (Twilio `To` field).
            **extra:     Any additional Twilio fields to include.

        Returns:
            The HTTP response object.
        """
        params = self._build_params(body, from_number, to_number, **extra)
        headers: dict[str, str] = {"Content-Type": "application/x-www-form-urlencoded"}

        if self.sign:
            signature = compute_twilio_signature(self.auth_token, self.webhook_url, params)
            headers["X-Twilio-Signature"] = signature

        return client.post(
            "/webhook",
            data=params,
            headers=headers,
        )

    def send_invalid_signature(
        self,
        client: TestClient,
        body: str,
        from_number: str,
    ) -> Any:
        """
        POST a webhook with a deliberately wrong signature.

        Use this to test that the SUT rejects tampered requests when
        signature validation is enforced.
        """
        params = self._build_params(body, from_number)
        return client.post(
            "/webhook",
            data=params,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Twilio-Signature": "invalid-signature-deliberately-wrong",
            },
        )


# ──────────────────────────────────────────────────────────────────────────────
# Functional shortcut
# ──────────────────────────────────────────────────────────────────────────────

def make_twilio_post(
    client: TestClient,
    auth_token: str,
    webhook_url: str,
    body: str,
    from_number: str,
    to_number: str = "whatsapp:+5511999999999",
) -> Any:
    """
    One-liner: create a TwilioSim and send one request.

    Useful for one-off tests that need a signed request but don't need
    the full TwilioSim object lifecycle.

    Args:
        client:      FastAPI TestClient.
        auth_token:  Twilio AUTH_TOKEN.
        webhook_url: Full URL (e.g. "http://testserver/webhook").
        body:        Message body text.
        from_number: Sender phone number.
        to_number:   Recipient number.

    Returns:
        HTTP response object.
    """
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
    sim = TwilioSim(auth_token=auth_token, webhook_url=webhook_url)
    return sim.send(client, body=body, from_number=from_number, to_number=to_number)
