-- tests/fixtures/seed.sql — Navi Test Seed Data
-- Password for all seed users: bcrypt hash of "harness-test-password"

-- Seed user 1: POST-ONBOARDING, no transactions
INSERT INTO usuarios (email, senha)
VALUES (
    '+55009900000901',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMlJbekRShaVbSNkbJtHMIhFHu'
)
ON CONFLICT (email) DO NOTHING;

-- Seed user 2: POST-ONBOARDING, with farmacia/mercado/lazer budgets
INSERT INTO usuarios (email, senha)
VALUES (
    '+55009900000902',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMlJbekRShaVbSNkbJtHMIhFHu'
)
ON CONFLICT (email) DO NOTHING;

-- Seed user 3: POST-ONBOARDING, with 2 transactions
INSERT INTO usuarios (email, senha)
VALUES (
    '+55009900000903',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMlJbekRShaVbSNkbJtHMIhFHu'
)
ON CONFLICT (email) DO NOTHING;
