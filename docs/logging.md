# Logging

Every line the service writes to stderr is one JSON object, including uvicorn's own startup and
shutdown lines (`--log-config config/uvicorn-logging.json`, used by the Docker image and `make run`).

```json
{"ts": "2026-01-15T12:00:00.123+00:00", "level": "INFO", "logger": "urlshort.api", "msg": "request",
 "request_id": "648c6910f58b42819023c06922f90ad9",
 "ctx": {"method": "POST", "path": "/api/v1/links", "status": 201, "duration_ms": 3.2}}
```

| Field | Meaning |
|---|---|
| `ts` | UTC timestamp |
| `level`, `logger`, `msg` | Standard logging fields |
| `request_id` | Matches the `X-Request-ID` response header; `-` outside a request |
| `ctx` | Structured fields passed by the code; can never overwrite the fields above |
| `exc` | Stack trace, for errors |

## Guarantees

- **Non-blocking:** log calls hand records to an in-memory queue; a background thread writes them,
  so slow output never slows a request. The queue is flushed on shutdown.
- **Request IDs survive the background thread:** captured on the request's thread before queueing.
- **Redaction:** values of sensitive `ctx` fields are replaced with `[REDACTED]` at any depth, before
  they reach the queue. Built-in keys: `authorization`, `x_api_key`, `api_key`, `password`, `secret`,
  `token`, `stats_token`, `x_stats_token`, `client_ip`, `ip`, `email`, `cookie`. Configured keys add to
  this list; they can never remove from it. Free-text messages are not scanned, so the code passes data
  as structured fields, never interpolated into the message.
- **No raw client IPs or tokens** are logged by the service.
- **Startup summary:** one `service configured` line lists the effective settings, with secrets shown
  only as `set` / `NOT SET`. A warning is logged while `URLSHORT_IP_SALT` is the built-in default.

## Configuration

```toml
[logging]
level = "INFO"                # DEBUG, INFO, WARNING, ERROR, CRITICAL  (env: URLSHORT_LOG_LEVEL)
redact_keys = []              # extra field names to redact            (env: URLSHORT_LOG_REDACT_KEYS)
request_sample_rate = 1.0     # share of successful request lines kept (env: URLSHORT_LOG_REQUEST_SAMPLE_RATE)
```

**Sampling** applies only to successful requests (status below 400); errors are always logged. The
keep/drop decision is a hash of the request ID, not a random number, so it is reproducible and any
other service that sees the same request ID makes the same decision.
