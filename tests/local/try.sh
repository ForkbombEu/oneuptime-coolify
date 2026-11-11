#!/bin/sh
# Run the full core bundle locally on http://localhost:7849 to click around.
# Usage: tests/local/try.sh up|down|logs [service]
set -eu
cd "$(dirname "$0")/../.."
compose="docker compose -p vigil-try -f docker-compose.yml -f tests/local/compose.override.yml --env-file tests/local/throwaway.env"
case "${1:-up}" in
  up)
    $compose up -d --wait --wait-timeout 600
    echo "Waiting for first-boot migrations..."
    for _ in $(seq 1 60); do
      scripts/smoke.py http://127.0.0.1:7849 >/dev/null 2>&1 && { echo "Ready: http://localhost:7849"; exit 0; }
      sleep 5
    done
    echo "Not ready after 5 minutes; check: tests/local/try.sh logs app" >&2
    exit 1
    ;;
  down) $compose down -v ;;
  logs) shift; $compose logs --tail 100 "$@" ;;
  *) echo "usage: $0 up|down|logs [service]" >&2; exit 2 ;;
esac
