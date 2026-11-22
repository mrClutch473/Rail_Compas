#!/usr/bin/env sh
# Update the server to the latest main: pull, build, migrate, restart.
set -eu
cd "$(dirname "$0")/.."

COMPOSE="docker compose --env-file .env -f deploy/docker-compose.prod.yml"

git pull --ff-only
$COMPOSE build
$COMPOSE up -d db
$COMPOSE run --rm web python manage.py migrate --noinput
$COMPOSE run --rm web python manage.py createcachetable
$COMPOSE run --rm web python manage.py seed_reference
$COMPOSE up -d
$COMPOSE ps
