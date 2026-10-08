# Security and identity

## API keys and roles

```bash
python -m urlshort.admin_cli keys create --name "team-a ci" --role owner --owner team-a   # printed once
python -m urlshort.admin_cli keys create --name alice --role admin
python -m urlshort.admin_cli keys list
python -m urlshort.admin_cli keys revoke <key_id>
```

Send a key as `Authorization: Bearer us_...` or `X-API-Key: us_...`.

| Caller | Create links | Read stats | Deactivate links | Audit actor |
|---|---|---|---|---|
| Anonymous | As `X-Owner` (unless `require_api_key = true`) | With the link's stats token | No | — |
| Owner key | As **its owner** (`X-Owner` ignored) | Its own links, no token needed | Its own links | `owner:<name>` |
| Admin key | Yes | Any link | Any link | `admin:<name>` |
| Bootstrap admin (`URLSHORT_ADMIN_API_KEY`) | Yes | Any link | Any link | `admin:bootstrap` |

- Keys are `us_<key_id>_<secret>`: only SHA-256 of the 256-bit secret is stored, compared in constant time;
  the plaintext is shown once and cannot be recovered. Revocation takes effect on the next request.
- A key that is sent but invalid or revoked is **401**, never treated as anonymous.
- Someone else's link looks like a missing link (404 on delete, 401 on stats): keys do not reveal which codes exist.
- Use the bootstrap admin key only to create named admin keys, then keep it in a vault (break-glass).

## Secrets

Every secret can be supplied as a file instead of an environment variable, so it never appears in the
environment or the image: `URLSHORT_ADMIN_API_KEY_FILE`, `URLSHORT_IP_SALT_FILE`, `URLSHORT_DATABASE_URL_FILE`,
`URLSHORT_REDIS_URL_FILE`, `URLSHORT_AUDIT_ANCHOR_KEY_FILE`. This is how Docker and Kubernetes secrets, and
HashiCorp Vault Agent (which renders secrets to files), deliver them; the service needs no Vault SDK. Setting
both a variable and its `_FILE` is an error, and so is an unreadable file. Secrets are never accepted from the
configuration file and appear only as `set` / `NOT SET` in the startup summary.

## Audit integrity: signed checkpoints

The hash chain detects editing or reordering records. It cannot detect rewriting the whole chain or deleting
its newest records, because the result is again a valid chain. Signed checkpoints close that gap:

```bash
URLSHORT_AUDIT_ANCHOR_KEY_FILE=/run/secrets/anchor python -m urlshort.admin_cli audit checkpoint > cp.json
python -m urlshort.admin_cli audit verify --checkpoint cp.json      # exit 0 if intact
```

A checkpoint records the record count, the hash of the last record and the time, signed (HMAC-SHA256) with a
key the database never sees. Verification fails if the signature is wrong, if records were removed
(truncation), if the checkpointed record changed (rewrite), or if the chain is broken. Ship checkpoints to
write-once storage (for example S3 Object Lock) on a schedule; an attacker who controls the database cannot
alter them. Tamper-evident remains the claim: the checkpoints make tampering detectable, not impossible.
