#!/usr/bin/env bash
# Black-box smoke test against a running instance. Grows with each feature commit.
set -euo pipefail
BASE="${1:-http://localhost:8000}"

for i in $(seq 1 30); do                      # wait until the service answers
  curl -fs "$BASE/healthz" >/dev/null && break
  sleep 1
  [ "$i" -eq 30 ] && { echo "service never became healthy"; exit 1; }
done

check() { [ "$2" = "$3" ] && echo "ok   $1 ($2)" || { echo "FAIL $1: expected $3, got $2"; exit 1; }; }

check "healthz status" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/healthz")" 200
check "healthz body" "$(curl -s "$BASE/healthz" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')" ok
check "unknown path" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/does-not-exist")" 404
echo "smoke test passed"
