#!/bin/bash
set -euo pipefail
echo "=== Navi Deploy Start ==="

APP_DIR=/home/__USER__/navimvp
mkdir -p "$APP_DIR/db"
cd "$APP_DIR"

printf '%s' '__COMPOSE__' | base64 -d > docker-compose.prod.yml
printf '%s' '__DB_SQL__'  | base64 -d > db/init.sql
printf '%s' '__ENV__'     | base64 -d > .env

printf '%s' '__ACR_PASS__' | base64 -d | docker login '__ACR_REG__' -u '__ACR_USER__' --password-stdin

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
  else
    COMPOSE_CMD="docker-compose"
    fi

    $COMPOSE_CMD -f docker-compose.prod.yml pull app
    $COMPOSE_CMD -f docker-compose.prod.yml up -d --no-deps --remove-orphans

    echo "=== Deploy complete ==="
