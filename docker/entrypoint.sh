#!/bin/sh
# Picks which Heimdall process this container runs. One image, many roles.
set -eu

role="${1:-}"
case "$role" in
  migrate)
    exec alembic upgrade head ;;
  api|worker|mcp|bridge)
    exec python "apps/$role/main.py" ;;
  # Run agents as modules so `agents.*` imports resolve from /app.
  huginn)
    exec python -m agents.huginn.runner ;;
  loki|heimdall)
    exec python -m "agents.$role.agent" ;;
  *)
    echo "usage: entrypoint.sh {migrate|api|worker|mcp|bridge|huginn|loki|heimdall}" >&2
    exit 64 ;;
esac
