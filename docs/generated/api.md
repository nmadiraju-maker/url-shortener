# API routes (static analysis fallback)

_OpenAPI generation was unavailable; routes extracted from source via AST._

| Route | Handler |
|---|---|
| `POST /api/v1/links` | `create_link` |
| `GET /api/v1/links/{code}` | `get_link` |
| `GET /api/v1/links/{code}/stats` | `link_stats` |
| `DELETE /api/v1/links/{code}` | `delete_link` |
| `GET /livez` | `livez` |
| `GET /healthz` | `livez` |
| `GET /readyz` | `readyz` |
| `GET /{code}` | `redirect` |
