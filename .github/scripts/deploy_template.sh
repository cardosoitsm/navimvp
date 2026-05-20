#!/bin/bash
set -euo pipefail
echo "=== Navi Deploy Start ==="

# Ensure OS firewall (ufw) allows port 8000
ufw allow 8000/tcp 2>/dev/null || true

APP_DIR=/home/__USER__/navimvp
mkdir -p "$APP_DIR/db"
cd "$APP_DIR"

printf '%s' '__COMPOSE__' | base64 -d > docker-compose.prod.yml
printf '%s' '__DB_SQL__'  | base64 -d > db/init.sql
printf '%s' '__ENV__'     | base64 -d > .env

printf '%s' '__ACR_PASS__' | base64 -d | docker login '__ACR_REG__' -u '__ACR_USER__' --password-stdin

# Ensure Docker Compose V2 is installed (avoids docker-compose v1 ContainerConfig bug)
if ! docker compose version >/dev/null 2>&1; then
  echo "Installing Docker Compose V2 binary..."
  mkdir -p /root/.docker/cli-plugins
  curl -SL "https://github.com/docker/compose/releases/download/v2.29.2/docker-compose-linux-x86_64" \
    -o /root/.docker/cli-plugins/docker-compose
  chmod +x /root/.docker/cli-plugins/docker-compose
fi
echo "Compose: $(docker compose version)"

docker compose -f docker-compose.prod.yml pull app
docker compose -f docker-compose.prod.yml up -d --no-deps --remove-orphans

echo "=== Deploy complete ==="
