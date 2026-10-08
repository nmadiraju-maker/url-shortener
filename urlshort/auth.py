"""API keys and principals.

Key format: `us_<key_id>_<secret>`. The key_id is a public lookup id; only SHA-256 of the secret is stored
(the secret is a 256-bit random value, so a fast hash is enough) and it is compared in constant time. A key
is shown once, when created.

Roles:
  owner  creates links as its owner (the X-Owner header is ignored), reads its own links' stats without
         per-link tokens, and can deactivate its own links
  admin  everything, on any link; its name is recorded as the audit actor ("admin:alice")
The configured URLSHORT_ADMIN_API_KEY still works as a break-glass key ("admin:bootstrap").
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime

from .errors import InvalidInput
from .storage import ApiKey, Repository

ROLES = ("owner", "admin")
KEY_RE = re.compile(r"^us_([0-9a-f]{12})_([A-Za-z0-9_-]{43})$")


@dataclass(frozen=True)
class Principal:
    name: str
    role: str
    owner: str | None

    @property
    def actor(self) -> str:
        """How this principal appears in the audit log."""
        return f"{self.role}:{self.name}"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def may_manage(self, link_owner: str) -> bool:
        return self.is_admin or (self.owner is not None and self.owner == link_owner)


BOOTSTRAP_ADMIN = Principal(name="bootstrap", role="admin", owner=None)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create_key(repo: Repository, *, name: str, role: str, owner: str | None, now: datetime) -> str:
    """Create, store (hashed) and return a new key. The plaintext is never stored and cannot be recovered."""
    if role not in ROLES:
        raise InvalidInput(f"role must be one of {ROLES}")
    if role == "owner" and not owner:
        raise InvalidInput("an owner key needs an owner")
    if not name.strip():
        raise InvalidInput("a key needs a name")
    key_id, secret = secrets.token_hex(6), secrets.token_urlsafe(32)
    repo.insert_api_key(ApiKey(key_id=key_id, name=name.strip(), owner=owner if role == "owner" else None,
                               role=role, secret_hash=_hash(secret), created_at=now))
    return f"us_{key_id}_{secret}"


def authenticate(repo: Repository, presented: str | None, *, bootstrap_admin_key: str = "") -> Principal | None:
    """The principal for a presented key, or None (missing, malformed, unknown, revoked or wrong secret)."""
    if not presented:
        return None
    if bootstrap_admin_key and hmac.compare_digest(presented, bootstrap_admin_key):
        return BOOTSTRAP_ADMIN
    match = KEY_RE.match(presented)
    if match is None:
        return None
    key = repo.get_api_key(match.group(1))
    if key is None or key.revoked_at is not None or not hmac.compare_digest(key.secret_hash, _hash(match.group(2))):
        return None
    return Principal(name=key.name, role=key.role, owner=key.owner)
