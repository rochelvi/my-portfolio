#!/usr/bin/env bash
set -euo pipefail

# работаем из каталога скрипта, а не из текущего cwd
cd "$(dirname "$0")"

BRANCH=master

git pull origin "$BRANCH"
docker compose up -d --build
