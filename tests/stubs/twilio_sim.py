"""
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
        }
        params.update(self.extra_params)
        params.update(kwargs)
        return params

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
    sim = TwilioSim(auth_token=auth_token, webhook_url=webhook_url)
    return sim.send(client, body=body, from_number=from_number, to_number=to_number)
