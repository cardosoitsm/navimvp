#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Navi MVP – QA Test Runner
# Run this from the navimvp/ directory:
#   chmod +x tests/run_tests.sh && ./tests/run_tests.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          Navi MVP – QA Test Suite Runner                     ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── 1. Install dependencies ──────────────────────────────────────────────────
echo "▶ Installing test dependencies..."
pip install --quiet --break-system-packages \
  pytest pytest-cov httpx fastapi psycopg2-binary \
  passlib python-jose pydantic-settings "pydantic[email]" \
  openai 2>/dev/null || true

# ── 2. Spin up a local Postgres for tests ────────────────────────────────────
# If you already have Postgres running on 5432, adjust these vars.
export DATABASE_HOST="${DATABASE_HOST:-localhost}"
export DATABASE_NAME="${DATABASE_NAME:-navimvp_test}"
export DATABASE_USER="${DATABASE_USER:-navimvp}"
export DATABASE_PASSWORD="${DATABASE_PASSWORD:-navimvppw}"
export DATABASE_PORT="${DATABASE_PORT:-5432}"
export SECRET_KEY="${SECRET_KEY:-test-secret-key-for-qa}"
export ALGORITHM="${ALGORITHM:-HS256}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-sk-test-dummy}"
export ACCOUNT_SID="${ACCOUNT_SID:-test-sid}"
export AUTH_TOKEN="${AUTH_TOKEN:-test-token}"
export TWILIO_NUMBER="${TWILIO_NUMBER:-whatsapp:+5511999999999}"
export APP_ENV="test"

echo "   DB: ${DATABASE_USER}@${DATABASE_HOST}:${DATABASE_PORT}/${DATABASE_NAME}"
echo ""

# ── 3. Run the test suite ─────────────────────────────────────────────────────
echo "▶ Running tests..."
python -m pytest tests/ \
  --tb=short \
  -v \
  --cov=app \
  --cov-report=term-missing \
  --cov-report=html:tests/coverage_html \
  -p no:warnings \
  "$@"

echo ""
echo "▶ Coverage report generated at: tests/coverage_html/index.html"
