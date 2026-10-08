# URL Shortener API v0.1.0

_Generated from the live OpenAPI schema by the Docs Agent._

| Method | Path | Summary | Responses |
|---|---|---|---|
| POST | `/api/v1/links` | Create Link | 201, 400, 401, 404, 409, 410, 422, 429, 503 |
| GET | `/api/v1/links/{code}` | Get Link | 200, 400, 401, 404, 409, 410, 422, 429, 503 |
| DELETE | `/api/v1/links/{code}` | Delete Link | 204, 400, 401, 404, 409, 410, 422, 429, 503 |
| GET | `/api/v1/links/{code}/stats` | Link Stats | 200, 400, 401, 404, 409, 410, 422, 429, 503 |
| GET | `/healthz` | Livez | 200 |
| GET | `/livez` | Livez | 200 |
| GET | `/readyz` | Readyz | 200 |
| GET | `/{code}` | Redirect | 200, 400, 401, 404, 409, 410, 422, 429, 503 |

## Schemas
- **CreateLinkRequest**: `url`, `custom_alias`, `ttl_seconds`, `max_clicks`
- **CreateLinkResponse**: `code`, `short_url`, `target_url`, `created_at`, `expires_at`, `is_active`, `click_count`, `max_clicks`, `stats_token`
- **ErrorBody**: `code`, `message`, `request_id`
- **ErrorResponse**: `error`
- **HTTPValidationError**: `detail`
- **LinkResponse**: `code`, `short_url`, `target_url`, `created_at`, `expires_at`, `is_active`, `click_count`, `max_clicks`
- **ReferrerCount**: `host`, `clicks`
- **StatsResponse**: `code`, `total_clicks`, `bot_clicks`, `clicks_by_day`, `clicks_by_hour`, `unique_visitors_by_day`, `top_referrers`, `agents`, `last_click_at`
- **ValidationError**: `loc`, `msg`, `type`, `input`, `ctx`
