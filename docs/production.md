# Running in production

Set `URLSHORT_ENV=production` (or `[service] environment = "production"`). The service then refuses to
start unless every check below passes, and reports **all** failing checks at once: one JSON log line at
level `CRITICAL` with a `problems` list, then exit code `2`. No traceback is printed; a bad configuration
is not a crash.

| Check | Why |
|---|---|
| `URLSHORT_IP_SALT` set, at least 16 characters | Visitor IDs are keyed hashes; a default or short key makes them guessable |
| `URLSHORT_ADMIN_API_KEY` set, at least 24 characters | Without it abusive links cannot be taken down; short keys can be guessed |
| `base_url` uses https | Short links over plain http can be tampered with in transit |
| `db_path` is a file | An in-memory database loses every link on restart |
| `expose_docs = false` | Unnecessary attack surface in production |
| `cors_allow_origins` has no `*` | Any website could call the API from a visitor's browser |
| Log level is not `DEBUG` | Verbose logs are more likely to leak data |
| `hsts_max_age` at least one day | Prevents downgrade to http |

The checks run when settings are loaded **and** inside `create_app`, so settings built in code cannot
bypass them.

## Minimal production environment

```bash
URLSHORT_ENV=production
URLSHORT_BASE_URL=https://sho.rt
URLSHORT_EXPOSE_DOCS=false
URLSHORT_ADMIN_API_KEY=<from your secret store, 24+ characters>
URLSHORT_IP_SALT=<from your secret store, 16+ characters>
URLSHORT_TRUSTED_PROXIES=<your load balancer CIDRs>
```

Generate secrets with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

The Docker image starts the service with `uvicorn urlshort.main:app_factory --factory`; that entry point
is what turns configuration errors into the single log line and exit code above.
