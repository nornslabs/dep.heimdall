#!/bin/sh
# Picks which Heimdall process this container runs. One image, many roles.
set -eu

role="${1:-}"
case "$role" in
  migrate)
    exec alembic upgrade head ;;
  # Run as modules so /app (not the script's folder) is on sys.path and
  # `apps.*` / `agents.*` imports resolve.
  api|worker|mcp|bridge)
    exec python -m "apps.$role.main" ;;
  huginn)
    exec python -m agents.huginn.runner ;;
  loki|heimdall)
    exec python -m "agents.$role.runner" ;;
  *)
    echo "usage: entrypoint.sh {migrate|api|worker|mcp|bridge|huginn|loki|heimdall}" >&2
    exit 64 ;;
esac
