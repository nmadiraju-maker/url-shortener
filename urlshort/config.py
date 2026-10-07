"""Runtime configuration.

Precedence (lowest -> highest): built-in defaults -> TOML config file -> environment variables.
The config file is chosen with URLSHORT_CONFIG=<path>; see config/urlshort.example.toml.

Rules:
  * Unknown sections or keys, wrong types and out-of-range values fail at startup. A typo must
    never silently fall back to a default.
  * Secrets (admin API key, IP-hash salt) come ONLY from the environment, never from the file,
    because config files get committed and copied around.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from .validation import KNOWN_SHORTENERS


class ConfigError(ValueError):
    """Invalid configuration; raised at startup."""


@dataclass(frozen=True)
class Settings:
    db_path: str = ":memory:"
    base_url: str = "http://localhost:8000"
    code_length: int = 7
    max_url_length: int = 2048
    max_ttl_seconds: int = 60 * 60 * 24 * 365
    create_rate_per_minute: int = 60        # steady rate per client for link creation
    create_burst: int = 10                  # back-to-back creations allowed from a full allowance
    redirect_rate_per_minute: int = 600     # per client; stops code scanning and click inflation
    redirect_burst: int = 100
    rate_limit_max_keys: int = 100_000      # clients tracked per limiter (bounded memory)
    admin_api_key: str = ""
    ip_hash_salt: str = "change-me"
    blocked_domains: frozenset[str] = field(default_factory=frozenset)
    known_shorteners: frozenset[str] = KNOWN_SHORTENERS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Load the file named by URLSHORT_CONFIG (if set), then apply environment overrides."""
        env = os.environ if env is None else env
        return load_settings(env.get("URLSHORT_CONFIG"), env)


# section -> key -> (Settings field, expected type)
FILE_SCHEMA: dict[str, dict[str, tuple[str, type]]] = {
    "service": {"base_url": ("base_url", str), "db_path": ("db_path", str), "code_length": ("code_length", int),
                "max_url_length": ("max_url_length", int), "max_ttl_seconds": ("max_ttl_seconds", int)},
    "ratelimit": {"create_rate_per_minute": ("create_rate_per_minute", int),
                  "create_burst": ("create_burst", int),
                  "redirect_rate_per_minute": ("redirect_rate_per_minute", int),
                  "redirect_burst": ("redirect_burst", int),
                  "max_tracked_clients": ("rate_limit_max_keys", int)},
    "validation": {"blocked_domains": ("blocked_domains", list), "known_shorteners": ("known_shorteners", list)},
}
SECRETS = frozenset({"admin_api_key", "ip_hash_salt"})
ENV_SCHEMA: dict[str, tuple[str, type]] = {
    "URLSHORT_DB_PATH": ("db_path", str),
    "URLSHORT_BASE_URL": ("base_url", str),
    "URLSHORT_CODE_LENGTH": ("code_length", int),
    "URLSHORT_MAX_URL_LENGTH": ("max_url_length", int),
    "URLSHORT_MAX_TTL_SECONDS": ("max_ttl_seconds", int),
    "URLSHORT_RATE_PER_MIN": ("create_rate_per_minute", int),
    "URLSHORT_CREATE_BURST": ("create_burst", int),
    "URLSHORT_REDIRECT_RATE_PER_MIN": ("redirect_rate_per_minute", int),
    "URLSHORT_REDIRECT_BURST": ("redirect_burst", int),
    "URLSHORT_RATE_LIMIT_MAX_CLIENTS": ("rate_limit_max_keys", int),
    "URLSHORT_BLOCKED_DOMAINS": ("blocked_domains", list),
    "URLSHORT_KNOWN_SHORTENERS": ("known_shorteners", list),
    "URLSHORT_ADMIN_API_KEY": ("admin_api_key", str),
    "URLSHORT_IP_SALT": ("ip_hash_salt", str),
}
MINIMUMS = {"code_length": 4, "max_url_length": 1, "max_ttl_seconds": 1, "create_rate_per_minute": 1,
            "create_burst": 1, "redirect_rate_per_minute": 1, "redirect_burst": 1, "rate_limit_max_keys": 1}


def _domains(items: Sequence[object], where: str) -> frozenset[str]:
    names = [i for i in items if isinstance(i, str)]
    if len(names) != len(items):
        raise ConfigError(f"{where}: expected a list of domain strings")
    return frozenset(n.strip().lower() for n in names if n.strip())


def _coerce(value: object, kind: type, where: str) -> object:
    if kind is int and (isinstance(value, bool) or not isinstance(value, int)):  # TOML `true` is an int in Python
        raise ConfigError(f"{where}: expected an integer, got {value!r}")
    if kind is str and not isinstance(value, str):
        raise ConfigError(f"{where}: expected a string, got {value!r}")
    if kind is list:
        if not isinstance(value, list):
            raise ConfigError(f"{where}: expected a list, got {value!r}")
        return _domains(value, where)
    return value


def read_config_file(path: str | Path) -> dict[str, object]:
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    values: dict[str, object] = {}
    for section, body in data.items():
        if section not in FILE_SCHEMA or not isinstance(body, dict):
            raise ConfigError(f"{path}: unknown section [{section}]; allowed: {sorted(FILE_SCHEMA)}")
        for key, value in body.items():
            if key in SECRETS:
                raise ConfigError(f"{path}: '{key}' is a secret; set it via the environment, not the file")
            if key not in FILE_SCHEMA[section]:
                allowed = sorted(FILE_SCHEMA[section])
                raise ConfigError(f"{path}: unknown key '{key}' in [{section}]; allowed: {allowed}")
            name, kind = FILE_SCHEMA[section][key]
            values[name] = _coerce(value, kind, f"[{section}].{key}")
    return values


def read_env(env: Mapping[str, str]) -> dict[str, object]:
    values: dict[str, object] = {}
    for var, (name, kind) in ENV_SCHEMA.items():
        if var not in env:
            continue
        raw = env[var]
        if kind is int:
            try:
                values[name] = int(raw)
            except ValueError as exc:
                raise ConfigError(f"{var}: expected an integer, got {raw!r}") from exc
        elif kind is list:
            values[name] = _domains(raw.split(","), var)
        else:
            values[name] = raw
    return values


def _check(values: dict[str, object]) -> None:
    for name, minimum in MINIMUMS.items():
        value = values.get(name)
        if isinstance(value, int) and value < minimum:
            raise ConfigError(f"{name} must be at least {minimum}, got {value}")
    base_url = values.get("base_url")
    if isinstance(base_url, str):
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ConfigError(f"base_url must be an absolute http(s) URL, got {base_url!r}")
        values["base_url"] = base_url.rstrip("/")


def load_settings(path: str | Path | None = None, env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    values: dict[str, object] = {"db_path": "urlshort.db"}  # a running service persists by default
    if path:
        values.update(read_config_file(path))
    values.update(read_env(env))
    _check(values)
    known = {f.name for f in fields(Settings)}
    # Every value was type-checked against FILE_SCHEMA / ENV_SCHEMA above, so the cast is safe.
    return Settings(**cast(dict[str, Any], {k: v for k, v in values.items() if k in known}))
