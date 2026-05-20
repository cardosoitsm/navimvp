"""
tests/stubs/openai_stub.py — OpenAI Completions API Stub Server
A lightweight FastAPI server that mimics the OpenAI Chat Completions API
(`POST /v1/chat/completions`) and returns scripted responses.

Why this exists
---------------
Mocking the Python OpenAI object with unittest.mock replaces the class
inside the process. It does not exercise the HTTP client configuration,
OPENAI_BASE_URL resolution, or JSON serialisation/deserialisation.

This stub runs as a real HTTP server. The SUT's OpenAI SDK makes real
HTTP calls — just to localhost:11435 instead of api.openai.com. Every
layer of the outbound interface is exercised.

Running standalone
------------------
    pip install fastapi uvicorn
    python tests/stubs/openai_stub.py

Or via Docker:
    docker-compose -f docker-compose.test.yml up openai-stub

Control API (available on the same port)
-----------------------------------------
    # Set which fixture the stub returns for the NEXT request
    POST /stub/set-response
    {"scenario": "despesa_transporte_50"}

    # Inspect all calls the stub has received
    GET /stub/calls

    # Reset call history and active scenario
    POST /stub/reset

    # Health check
    GET /stub/health

Fixture file
------------
Scripted responses live in tests/fixtures/openai_responses.json.
Each key is a scenario name; the value is the raw content string
that will be placed inside choices[0].message.content.

Default behaviour
-----------------
If no scenario is set via /stub/set-response, the stub returns an
empty transaction list: `[]`. This models the "unrecognised message"
path in the SUT.

Usage in tests (fast path — no Docker)
---------------------------------------
For most tests, use unittest.mock instead:
    from tests.harness import make_openai_mock, MOCK_OPENAI_PATCH
    with patch(MOCK_OPENAI_PATCH, make_openai_mock("despesa", "transporte", 50.0)):
        resp = post_webhook(client, "Gastei R$50 no Uber", phone)

Use this stub server only when you need to exercise the HTTP path
or run the full docker-compose.test.yml harness.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ──────────────────────────────────────────────────────────────────────────────
# Load fixture file
# ──────────────────────────────────────────────────────────────────────────────

_FIXTURES_PATH = Path(__file__).parent.parent / "fixtures" / "openai_responses.json"

def _load_fixtures() -> dict[str, str]:
    """Load scripted responses from the fixture file."""
    if _FIXTURES_PATH.exists():
        with open(_FIXTURES_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ──────────────────────────────────────────────────────────────────────────────
# Stub state (in-memory, reset on each test run)
# ──────────────────────────────────────────────────────────────────────────────

_state: dict[str, Any] = {
    "active_scenario": None,   # name of next scripted response to return
    "call_log": [],            # list of {timestamp, model, messages, scenario_used, response}
    "fixtures": _load_fixtures(),
}

DEFAULT_CONTENT = "[]"  # empty transaction list = unrecognised message


def _make_completion_response(content: str, model: str = "gpt-4.1-mini") -> dict:
    """Build a minimal OpenAI-compatible chat completion response."""
    return {
        "id": f"stub-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": len(content.split()),
            "total_tokens": 10 + len(content.split()),
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# FastAPI app
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Navi OpenAI Stub Server", version="1.0.0")


# ── OpenAI-compatible completions endpoint ────────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse:
    """
    Mimic POST /v1/chat/completions.

    Returns the scripted response for the active scenario, or the default
    empty-transaction response if no scenario is set.
    """
    body = await request.json()
    model = body.get("model", "gpt-4.1-mini")
    messages = body.get("messages", [])

    scenario = _state["active_scenario"]
    content = (
        _state["fixtures"].get(scenario, DEFAULT_CONTENT)
        if scenario
        else DEFAULT_CONTENT
    )

    _state["call_log"].append({
        "timestamp": time.time(),
        "model": model,
        "messages": messages,
        "scenario_used": scenario,
        "response_content": content,
    })

    # Clear scenario after use — one scripted response per set-response call
    _state["active_scenario"] = None

    return JSONResponse(content=_make_completion_response(content, model))


# ── Stub control API ──────────────────────────────────────────────────────────

@app.post("/stub/set-response")
async def set_response(request: Request) -> JSONResponse:
    """
    Set the scenario (scripted response) for the NEXT completions call.

    Body: {"scenario": "despesa_transporte_50"}

    The scenario name must match a key in tests/fixtures/openai_responses.json.
    If the scenario is unknown, the stub logs a warning and uses the default.
    """
    body = await request.json()
    scenario = body.get("scenario")
    if scenario and scenario not in _state["fixtures"]:
        return JSONResponse(
            status_code=400,
            content={
                "error": f"Unknown scenario '{scenario}'. "
                         f"Available: {list(_state['fixtures'].keys())}"
            },
        )
    _state["active_scenario"] = scenario
    return JSONResponse({"ok": True, "active_scenario": scenario})


@app.get("/stub/calls")
async def get_calls() -> JSONResponse:
    """Return the full call log. Each entry records what the SUT sent."""
    return JSONResponse({
        "total_calls": len(_state["call_log"]),
        "calls": _state["call_log"],
    })


@app.post("/stub/reset")
async def reset() -> JSONResponse:
    """Clear call history and active scenario."""
    _state["active_scenario"] = None
    _state["call_log"] = []
    _state["fixtures"] = _load_fixtures()  # reload from disk
    return JSONResponse({"ok": True, "message": "Stub state reset."})


@app.get("/stub/health")
async def health() -> JSONResponse:
    """Health check — returns 200 when the stub is ready."""
    return JSONResponse({
        "status": "ok",
        "active_scenario": _state["active_scenario"],
        "total_calls": len(_state["call_log"]),
        "fixtures_loaded": len(_state["fixtures"]),
    })


# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", "11435"))
    print(f"OpenAI stub server starting on port {port}")
    print(f"Fixtures loaded: {list(_state['fixtures'].keys())}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
