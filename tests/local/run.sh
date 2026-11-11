#!/bin/sh
# Start the core services of the rendered bundle locally, smoke check them, tear down.
# Usage: tests/local/run.sh [--keep]
set -eu
cd "$(dirname "$0")/../.."
compose="docker compose -p vigil-smoke -f docker-compose.yml -f tests/local/compose.override.yml --env-file tests/local/throwaway.env"
[ "${1:-}" = "--keep" ] || trap '$compose down -v' EXIT
$compose up -d --wait --wait-timeout 600 app ingress postgres clickhouse valkey
status=0
for _ in $(seq 1 30); do
  scripts/smoke.py http://127.0.0.1:7849 && { status=0; break; } || status=$?
  sleep 10
done
exit $status
