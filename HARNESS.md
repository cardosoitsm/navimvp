# Navi Test Harness Engineering Document

> **This document is the authoritative description of the Navi test infrastructure.**
> Every engineer, agent, and CI pipeline must understand this boundary before
> writing a test, adding a stub, or changing a fixture.

---

## 1. What Is a Test Harness

A test harness is the complete infrastructure that makes a system testable in
a controlled, repeatable, and deterministic way. It is not a set of test
<<<<<<< HEAD
functions -- it is the **scaffold** those functions run inside.
=======
functions — it is the **scaffold** those functions run inside.
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)

The harness for Navi has four engineering layers:

```
<<<<<<< HEAD
+---------------------------------------------------------------------+
|  Layer 4 -- Test Oracle                                             |
|  How correctness is decided: HTTP status, response body, DB state   |
+---------------------------------------------------------------------+
|  Layer 3 -- Test Controller                                         |
|  pytest + conftest.py + fixtures + docker-compose.test.yml          |
+---------------------------------------------------------------------+
|  Layer 2 -- Interface Adapters (Stubs & Simulators)                 |
|  tests/stubs/openai_stub.py  .  tests/stubs/twilio_sim.py          |
+---------------------------------------------------------------------+
|  Layer 1 -- System Under Test (SUT)                                 |
|  app/ -- FastAPI + PostgreSQL + uvicorn                             |
+---------------------------------------------------------------------+
=======
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 4 — Test Oracle                                              │
│  How correctness is decided: HTTP status, response body, DB state  │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 3 — Test Controller                                          │
│  pytest + conftest.py + fixtures + docker-compose.test.yml          │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2 — Interface Adapters (Stubs & Simulators)                  │
│  tests/stubs/openai_stub.py  ·  tests/stubs/twilio_sim.py          │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 1 — System Under Test (SUT)                                  │
│  app/ — FastAPI + PostgreSQL + uvicorn                              │
└─────────────────────────────────────────────────────────────────────┘
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
```

---

## 2. System Under Test (SUT) Boundary

The SUT is the `app/` directory running as a FastAPI/uvicorn process.

### Inside the boundary (owned, tested as real code)

| Component | Location | Role |
|---|---|---|
| HTTP API | `app/main.py` | Webhook, chat, auth, admin endpoints |
| Auth | `app/auth.py` | JWT creation/validation, bcrypt |
| Config | `app/config.py` | Pydantic settings from env vars |
| DB layer | `app/db.py` | psycopg2 connection, schema migration |
| Services | `app/services/` | All business logic modules |
| Schemas | `app/schemas.py` | Pydantic request/response models |

### Outside the boundary (replaced by stubs in tests)

| External System | Production Role | Test Replacement |
|---|---|---|
| OpenAI API | Transaction classification, card name extraction | `tests/stubs/openai_stub.py` |
| Twilio WhatsApp | Inbound webhook sender, outbound message delivery | `tests/stubs/twilio_sim.py` |
| PostgreSQL (prod) | Persistent user data | Ephemeral PostgreSQL 15 container |
| Azure infrastructure | Hosting, registry, CI/CD | Not tested in unit/integration scope |

### SUT boundary diagram

```
<<<<<<< HEAD
                +--------------------------------------+
                |          SYSTEM UNDER TEST           |
  Twilio        |                                      |        PostgreSQL
  Simulator --->|  POST /webhook                       |<------>  (test)
                |      |                               |
  pytest        |  app/main.py                         |
  TestClient -->|      |                               |
                |  app/services/                        |
                |      |                               |
  OpenAI        |  app/db.py                           |
  Stub      <---|      (psycopg2)                      |
  (HTTP)        |                                      |
                +--------------------------------------+
=======
                ┌──────────────────────────────────────┐
                │          SYSTEM UNDER TEST            │
  Twilio        │                                      │        PostgreSQL
  Simulator ───▶│  POST /webhook                       │◀──────▶  (test)
                │      ↓                               │
  pytest        │  app/main.py                         │
  TestClient ───▶│      ↓                               │
                │  app/services/                        │
                │      ↓                               │
  OpenAI        │  app/db.py                           │
  Stub      ◀───│      (psycopg2)                      │
  (HTTP)        │                                      │
                └──────────────────────────────────────┘
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
```

**Key design decision:** The OpenAI stub runs as a separate HTTP process on
`localhost:11435`. The SUT points its `OPENAI_BASE_URL` env var at the stub,
<<<<<<< HEAD
so the real OpenAI SDK makes real HTTP calls -- just to our controlled server.
=======
so the real OpenAI SDK makes real HTTP calls — just to our controlled server.
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
This exercises the HTTP client code path, not just mock return values.

The Twilio simulator (`twilio_sim.py`) generates valid HMAC-SHA1 signatures
so the SUT's signature validation middleware is exercised with correctly-signed
requests, not bypassed.

---

## 3. Interface Catalog

Every interface the SUT exposes or consumes is documented here. Each interface
has exactly one stub or real implementation used in tests.

### 3.1 Inbound interfaces (SUT receives)

| Interface | Protocol | Auth | Test implementation |
|---|---|---|---|
| WhatsApp webhook | `POST /webhook` form-encoded | Twilio HMAC-SHA1 header (optional, off by default) | `tests/stubs/twilio_sim.py` via `TestClient` |
| Chat API | `POST /chat` JSON + JWT Bearer | JWT signed with `SECRET_KEY` | `tests/conftest.py auth_header()` |
| Auth endpoints | `POST /register`, `POST /login` | None | Direct `TestClient` calls |
| Admin endpoint | `POST /admin/reset-user` JSON | `X-Admin-Key` header | Direct `TestClient` calls |
| Health check | `GET /health` | None | Direct `TestClient` calls |

### 3.2 Outbound interfaces (SUT calls)

| Interface | Protocol | Test replacement | Location |
|---|---|---|---|
| OpenAI completions | HTTPS JSON | OpenAI Stub Server | `tests/stubs/openai_stub.py` |
| PostgreSQL | TCP psycopg2 | Ephemeral PostgreSQL 15 | `docker-compose.test.yml` |
| Twilio outbound SMS | HTTPS (Twilio SDK) | Not tested (TwiML response only) | Out of scope |

### 3.3 Response format for webhook

The `/webhook` endpoint returns TwiML XML. Tests must check:
- `content-type` contains `xml`
- Body contains expected Portuguese keywords
- No `<Response/>` empty body (indicates unhandled case)

---

## 4. Environment Specification

### 4.1 Test environment variables

All test runs use these values. They are set in `conftest.py` and in the
CI workflow (`ci.yml`). Never use production values in tests.

```bash
DATABASE_HOST=localhost
DATABASE_PORT=5432        # 5433 when running alongside prod DB locally
DATABASE_NAME=navimvp_test
DATABASE_USER=navimvp
DATABASE_PASSWORD=navimvppw
SECRET_KEY=qa-test-secret-<timestamp>   # unique per run to prevent token reuse
ADMIN_SECRET_KEY=qa-admin-secret-<timestamp>
ALGORITHM=HS256
OPENAI_API_KEY=sk-stub-key              # accepted by the OpenAI stub
OPENAI_BASE_URL=http://localhost:11435/v1  # points to the stub (when using stub server)
ACCOUNT_SID=test-sid
AUTH_TOKEN=test-token                   # used for Twilio signature validation
TWILIO_NUMBER=whatsapp:+5511999999999
```

<<<<<<< HEAD
### 4.2 Local test run (fast path -- mocks, no stub server)

This is the default mode used by `pytest` directly. OpenAI is mocked at the
Python object level using `unittest.mock.patch`. Fastest -- no Docker needed
=======
### 4.2 Local test run (fast path — mocks, no stub server)

This is the default mode used by `pytest` directly. OpenAI is mocked at the
Python object level using `unittest.mock.patch`. Fastest — no Docker needed
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
beyond PostgreSQL.

```bash
# Start PostgreSQL only
docker run --rm -d --name navi-test-db \
  -e POSTGRES_USER=navimvp \
  -e POSTGRES_PASSWORD=navimvppw \
  -e POSTGRES_DB=navimvp_test \
  -p 5432:5432 postgres:15

# Run suite
SECRET_KEY=qa-test-secret \
ADMIN_SECRET_KEY=qa-admin-secret \
DATABASE_HOST=localhost \
DATABASE_NAME=navimvp_test \
OPENAI_API_KEY=sk-dummy \
pytest tests/ -v --tb=short --cov=app

# Teardown
docker stop navi-test-db
```

### 4.3 Full harness run (stub server mode)

Uses `docker-compose.test.yml` to bring up PostgreSQL + OpenAI stub server.
<<<<<<< HEAD
The SUT runs as a subprocess pointed at both.
=======
The SUT runs as a subprocess pointed at both. Used for integration-level
validation where real HTTP calls to the OpenAI stub server matter.
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)

```bash
docker-compose -f docker-compose.test.yml up -d
sleep 5
OPENAI_BASE_URL=http://localhost:11435/v1 \
pytest tests/ -v --tb=short --cov=app -m "not stub_required or stub_required"
docker-compose -f docker-compose.test.yml down
```

### 4.4 CI environment

Managed by `.github/workflows/ci.yml`. Uses GitHub Actions `services:` for
PostgreSQL (no Docker Compose needed). OpenAI calls are mocked at the Python
level. Does not use the stub server.

---

## 5. Stub Catalog

### 5.1 OpenAI Stub Server (`tests/stubs/openai_stub.py`)

**Purpose:** Replace the OpenAI `https://api.openai.com` endpoint with a
locally controlled FastAPI server that returns scripted responses.

**Why not just mock the Python object?**
Mocking `unittest.mock.patch("app.services.chat.OpenAI")` replaces the class
inside the Python process. It does not exercise:
- The HTTP client configuration (timeouts, retries)
- The `OPENAI_BASE_URL` environment variable path
- Serialisation/deserialisation of the OpenAI response format

The stub server exercises all of the above.

**How it works:**

```
SUT (app/services/chat.py)
<<<<<<< HEAD
  +-- OpenAI(api_key=..., base_url=OPENAI_BASE_URL)
        +-- POST http://localhost:11435/v1/chat/completions
              +-- openai_stub.py
                    +-- returns scripted JSON from tests/fixtures/openai_responses.json
=======
  └── OpenAI(api_key=..., base_url=OPENAI_BASE_URL)
        └── POST http://localhost:11435/v1/chat/completions
              └── openai_stub.py
                    └── returns scripted JSON from tests/fixtures/openai_responses.json
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
```

**Control API:**

```bash
# Set the next response the stub will return
POST http://localhost:11435/stub/set-response
Content-Type: application/json
{"scenario": "despesa_transporte_50"}

# Check call history
GET http://localhost:11435/stub/calls
```

**Default behaviour:** If no response is set, returns a "not a transaction"
empty-list response `[]`.

### 5.2 Twilio Webhook Simulator (`tests/stubs/twilio_sim.py`)

**Purpose:** Generate correctly-signed Twilio webhook POST requests so the
SUT's optional HMAC signature validation is exercised, not bypassed.

**How it works:**
Twilio signs webhook requests with HMAC-SHA1 using the `AUTH_TOKEN` and the
full request URL. The simulator generates this signature and injects it as
the `X-Twilio-Signature` header.

**Usage in tests:**

```python
from tests.stubs.twilio_sim import TwilioSim

sim = TwilioSim(auth_token="test-token", webhook_url="http://testserver/webhook")
resp = sim.send(client, body="Oi", from_number="+5511900000001")
```

**When to use:** Use `TwilioSim` when testing signature validation logic.
<<<<<<< HEAD
For most tests, use `post_webhook()` from `tests/harness.py` directly --
=======
For most tests, use `post_webhook()` from `tests/harness.py` directly —
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
it bypasses signature validation for speed.

---

## 6. Test Data Management

### 6.1 Phone number pool

All test phone numbers come from `tests/harness.py::test_phone(N)`.

<<<<<<< HEAD
Format: `+55009XXXXXXXX` -- area code `00` does not exist in Brazil.
=======
Format: `+55009XXXXXXXX` — area code `00` does not exist in Brazil.
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
These numbers can never be confused with real users.

Allocation:

| Range | Owner |
|---|---|
<<<<<<< HEAD
| 1-100 | `test_webhook.py` |
| 101-200 | `test_security.py` |
| 201-300 | `test_chat.py` (uses email-based users) |
| 301-400 | `test_scenarios.py` (scenario classes) |
| 901-910 | Reserved for fixtures (`onboarded_user`, etc.) |
=======
| 1–100 | `test_webhook.py` |
| 101–200 | `test_security.py` |
| 201–300 | `test_chat.py` (uses email-based users) |
| 301–400 | `test_scenarios.py` (scenario classes) |
| 901–910 | Reserved for fixtures (`onboarded_user`, etc.) |
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)

### 6.2 Seed data (`tests/fixtures/seed.sql`)

Contains pre-built users in known states for tests that need a specific
starting point without running through conversation flows. Applied manually
in full-harness mode; not used in the default fast-path run.

### 6.3 DB reset strategy

- **Per-test:** `client` fixture in `conftest.py` drops and recreates all
  tables before each test function. This is the default and guarantees
  isolation between tests.
- **Per-session:** `db_setup` fixture creates the schema once. Used only
  for read-only tests where recreation overhead matters.

**Rule:** Never share state between tests through the database. Each test
must be able to run in isolation and in any order.

---

## 7. Test Controller

The test controller is `pytest` configured by:

| File | Role |
|---|---|
| `tests/conftest.py` | Session fixtures, DB setup, global harness fixture registration |
| `tests/harness.py` | Conversation drivers, mock factories, assertion helpers, phone pool |
<<<<<<< HEAD
| `tests/TRACEABILITY.md` | Requirements to test mapping (the spec) |
=======
| `tests/TRACEABILITY.md` | Requirements → test mapping (the spec) |
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
| `docker-compose.test.yml` | Full-harness environment orchestration |
| `.github/workflows/ci.yml` | CI controller: runs tests on every PR and push to main |

---

## 8. Test Oracle

A test passes when:

1. **HTTP status** is the expected value (200, 400, 401, 403, 422)
2. **Response body** contains expected Portuguese keywords (case-insensitive)
3. **Database state** reflects the expected side effects (when checked)
<<<<<<< HEAD
4. **No 500 errors** at any point -- a 500 is always a blocker
=======
4. **No 500 errors** at any point — a 500 is always a blocker
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)

The oracle is implemented in `tests/harness.py::assert_webhook_ok()` and
`assert_webhook_error()`, plus inline `assert` statements in test functions.

### What the oracle does NOT check (explicitly out of scope)

- Twilio outbound message delivery (tested via Twilio sandbox manually)
- OpenAI response quality (the stub controls this deterministically)
- Azure infrastructure health (validated by QA agent post-deploy)
<<<<<<< HEAD
- Exact Portuguese wording (keywords only -- wording is a product decision)
=======
- Exact Portuguese wording (keywords only — wording is a product decision)
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)

---

## 9. Coverage Requirements

| Scope | Minimum | Enforced by |
|---|---|---|
| Overall `app/` | 65% | CI threshold in `ci.yml` |
| Changed files (per PR) | 70% | QA agent checklist |
| Security-critical paths (`auth.py`, `db.py`) | 80% | QA agent checklist |
<<<<<<< HEAD
| Scenario A-H coverage | 100% scenario steps | `test_scenarios.py` |
=======
| Scenario A–H coverage | 100% scenario steps | `test_scenarios.py` |
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)

---

## 10. What Changes Require Harness Updates

| Change type | Harness impact |
|---|---|
<<<<<<< HEAD
| New endpoint added | Add to interface catalog (S3), add tests |
| New external service integrated | Add to boundary diagram (S2), create stub |
=======
| New endpoint added | Add to interface catalog (§3), add tests |
| New external service integrated | Add to boundary diagram (§2), create stub |
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
| New onboarding state added | Update `complete_onboarding()` in `harness.py`, update scenario A |
| New budget threshold | Add scenario D test step |
| New table with `user_id` column | Update `LGPD` test in `test_scenarios.py`, update seed.sql |
| SEC rule added/changed | Update `TRACEABILITY.md`, update security scenario |
<<<<<<< HEAD
| Phone number range exhausted | Extend pool allocation table in S6.1 |

---

*Maintained by: QA Agent | Last updated: 2026-05-07*
=======
| Phone number range exhausted | Extend pool allocation table in §6.1 |

---

*Maintained by: QA Agent | Last updated: 2026-05-06*
>>>>>>> bfa62b0 (Add Obsidian workspace and AI architecture files)
