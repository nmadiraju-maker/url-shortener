# API

Interactive documentation is served at `/docs` (OpenAPI at `/openapi.json`).

| Method & path | Purpose | Success | Errors |
|---|---|---|---|
| `POST /api/v1/links` | Create a short link | 201 created; 200 if an identical permanent link is reused | 400, 409 |
| `GET /{code}` | Redirect to the target | 307 with `Cache-Control: no-store` | 404, 410 |
| `GET /api/v1/links/{code}` | Link details | 200 | 404 |
| `DELETE /api/v1/links/{code}` | Deactivate a link (admin, `X-API-Key`) | 204 | 401, 404 |
| `GET /healthz` | Liveness | 200 | |
| `GET /readyz` | Readiness (database reachable) | 200 | 503 |

## Create a link

```bash
curl -s -X POST localhost:8000/api/v1/links -H 'content-type: application/json' \
  -H 'x-owner: team-a' -d '{"url": "https://example.com/spring", "custom_alias": "spring-sale", "ttl_seconds": 86400}'
```

| Field | Required | Rules |
|---|---|---|
| `url` | yes | http(s) only; no credentials; no private/internal hosts; no other shorteners; max length configurable |
| `custom_alias` | no | 3-32 letters, digits, `-`, `_`; service paths are reserved |
| `ttl_seconds` | no | 1 second up to the configured maximum (default 1 year) |

Unknown fields are rejected. Without an alias or TTL, repeating the same URL for the same owner returns the
existing link (200) instead of creating a duplicate, so client retries are safe.

`X-Owner` identifies the creating team. It is **not authenticated yet** (owner API keys are planned); treat it
as a label, not a security boundary.

## Errors

Every error, including unexpected ones, has the same shape:

```json
{"error": {"code": "link_expired", "message": "link 'abc1234' has expired", "request_id": "648c69..."}}
```

`code` is stable for programs to switch on; `request_id` matches the `X-Request-ID` response header and the
service logs. Send your own `X-Request-ID` to correlate across systems.

| Status | `code` |
|---|---|
| 400 | `invalid_input` |
| 401 | `unauthorized` |
| 404 | `not_found` |
| 409 | `alias_conflict` |
| 410 | `link_expired` |
| 500 | `internal_error` (details are logged, never returned) |
| 503 | `code_generation_failed` |
