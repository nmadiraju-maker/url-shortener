#!/usr/bin/env bash
# Black-box smoke test against a running instance. Grows with each feature commit.
#   URLSHORT_ADMIN_API_KEY=... ./scripts/smoke_test.sh [base-url]
set -euo pipefail
BASE="${1:-http://localhost:8000}"
KEY="${URLSHORT_ADMIN_API_KEY:?set URLSHORT_ADMIN_API_KEY to the key the service was started with}"

for i in $(seq 1 30); do                      # wait until the service is ready
  curl -fs "$BASE/readyz" >/dev/null && break
  sleep 1
  [ "$i" -eq 30 ] && { echo "service never became ready"; exit 1; }
done

check() { [ "$2" = "$3" ] && echo "ok   $1 ($2)" || { echo "FAIL $1: expected $3, got $2"; exit 1; }; }
status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
post() { curl -s -X POST "$BASE/api/v1/links" -H 'content-type: application/json' -d "$1"; }
field() { python3 -c 'import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])' "$1" "$2"; }

check "healthz" "$(status "$BASE/healthz")" 200
check "readyz" "$(status "$BASE/readyz")" 200

body=$(post '{"url":"https://example.com/smoke"}')
code=$(field "$body" code)
check "create link" "$(field "$body" target_url)" "https://example.com/smoke"
check "redirect status" "$(status "$BASE/$code")" 307
check "redirect target" "$(curl -s -o /dev/null -w '%{redirect_url}' "$BASE/$code")" "https://example.com/smoke"
check "idempotent create" "$(field "$(post '{"url":"https://example.com/smoke"}')" code)" "$code"
check "link details" "$(status "$BASE/api/v1/links/$code")" 200
token=$(field "$body" stats_token)
check "stats need a token" "$(status "$BASE/api/v1/links/$code/stats")" 401
check "stats with token" "$(status -H "x-stats-token: $token" "$BASE/api/v1/links/$code/stats")" 200
curl -s -o /dev/null -A "Mozilla/5.0 Chrome/126.0" "$BASE/$code"     # one browser-like click
stats=$(curl -s -H "x-stats-token: $token" "$BASE/api/v1/links/$code/stats")
check "browser click counted" "$(field "$stats" total_clicks)" 1
check "curl clicks are bots" "$(field "$stats" bot_clicks)" 2             # curl's User-Agent marks it a bot
check "unknown code" "$(status "$BASE/nope-not-here")" 404
check "ssrf blocked" "$(status -X POST "$BASE/api/v1/links" -H 'content-type: application/json' \
  -d '{"url":"http://169.254.169.254/"}')" 400
check "unknown field rejected" "$(status -X POST "$BASE/api/v1/links" -H 'content-type: application/json' \
  -d '{"url":"https://example.com","ttl":60}')" 400
check "admin needs key" "$(status -X DELETE "$BASE/api/v1/links/$code")" 401
check "admin delete" "$(status -X DELETE -H "x-api-key: $KEY" "$BASE/api/v1/links/$code")" 204
check "gone after delete" "$(status "$BASE/$code")" 404
echo "smoke test passed"
