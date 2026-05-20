-- tests/fixtures/seed.sql — Navi Test Seed Data
-- ================================================
-- Deterministic test users in known states.
-- Applied in full-harness mode (docker-compose.test.yml).
-- NOT applied in the default fast-path pytest run (conftest.py resets the DB
-- per test using init_db() instead).
--
-- Usage:
--   psql -U navimvp -d navimvp_test -f tests/fixtures/seed.sql
--
-- Password for all seed users: bcrypt hash of "harness-test-password"
-- (generated with passlib.hash.bcrypt.hash("harness-test-password"))
-- Never use real passwords in fixtures.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Seed user 1: POST-ONBOARDING, no transactions ────────────────────────────
-- Phone: +55009900000901 (test_phone(901) — harness fixture range)
-- State: onboarding_complete
INSERT INTO usuarios (email, senha)
VALUES (
    '+55009900000901',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMlJbekRShaVbSNkbJtHMIhFHu'
)
ON CONFLICT (email) DO NOTHING;

-- ── Seed user 2: POST-ONBOARDING, with farmacia/mercado/lazer budgets ─────────
-- Phone: +55009900000902 (test_phone(902) — user_with_budget fixture)
INSERT INTO usuarios (email, senha)
VALUES (
    '+55009900000902',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMlJbekRShaVbSNkbJtHMIhFHu'
)
ON CONFLICT (email) DO NOTHING;

-- ── Seed user 3: POST-ONBOARDING, with 2 transactions ────────────────────────
-- Phone: +55009900000903 (test_phone(903) — user_with_transactions fixture)
INSERT INTO usuarios (email, senha)
VALUES (
    '+55009900000903',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMlJbekRShaVbSNkbJtHMIhFHu'
)
ON CONFLICT (email) DO NOTHING;

-- Note: configuracoes_usuario rows for these users are created by init_db()
-- during the test run. This file only seeds the usuarios table.
-- The full state (onboarding_state = 'onboarding_complete') is set by the
-- conftest.py fixture functions that drive the conversation flows.
