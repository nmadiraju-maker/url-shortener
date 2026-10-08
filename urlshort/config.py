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

import ipaddress
import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from .logging_setup import DEFAULT_REDACT_KEYS, LEVELS
from .validation import KNOWN_SHORTENERS


class ConfigError(ValueError):
    """Invalid configuration; raised at startup. `problems` lists each issue when there are several."""

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = problems or [message]


ENVIRONMENTS = ("development", "production")
MIN_ADMIN_KEY_LENGTH = 24
MIN_IP_SALT_LENGTH = 16


@dataclass(frozen=True)
class Settings:
    environment: str = "development"        # "production" turns on the startup safety checks below
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
    log_level: str = "INFO"
    log_redact_keys: frozenset[str] = DEFAULT_REDACT_KEYS
    log_request_sample_rate: float = 1.0    # share of successful (<400) request lines kept; errors always kept
    trusted_proxies: frozenset[str] = field(default_factory=frozenset)      # CIDRs allowed to set X-Forwarded-For
    cors_allow_origins: frozenset[str] = field(default_factory=frozenset)   # empty = CORS disabled
    expose_docs: bool = True                # /docs, /redoc, /openapi.json
    hsts_max_age: int = 31_536_000          # sent only when base_url is https; 0 disables

    def summary(self) -> dict[str, object]:
        """Settings safe to log: secrets reported only as set / not set, sets as sorted lists."""
        out: dict[str, object] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in SECRETS:
                out[f.name] = "set" if value and value != DEFAULT_IP_SALT else "NOT SET"
            elif isinstance(value, frozenset):
                out[f.name] = sorted(value)
            else:
                out[f.name] = value
        return out
    admin_api_key: str = ""
    database_url: str = ""                  # postgresql://... (env only: may hold a password); empty = SQLite
    redis_url: str = ""                     # redis://... (env only); enables the cache, Bloom filter, shared limits
    cache_ttl_seconds: int = 60             # how long a cached link may be served (click_count may lag this much)
    analytics_mode: str = "sync"            # "events": clicks via transactional outbox + workers (docs/events.md)
    ip_hash_salt: str = "change-me"         # DEFAULT_IP_SALT; replace via URLSHORT_IP_SALT
    blocked_domains: frozenset[str] = field(default_factory=frozenset)
    known_shorteners: frozenset[str] = KNOWN_SHORTENERS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Load the file named by URLSHORT_CONFIG (if set), then apply environment overrides."""
        env = os.environ if env is None else env
        return load_settings(env.get("URLSHORT_CONFIG"), env)


DEFAULT_IP_SALT = "change-me"

# section -> key -> (Settings field, expected type)
FILE_SCHEMA: dict[str, dict[str, tuple[str, type]]] = {
    "service": {"environment": ("environment", str),
                "base_url": ("base_url", str), "db_path": ("db_path", str), "code_length": ("code_length", int),
                "max_url_length": ("max_url_length", int), "max_ttl_seconds": ("max_ttl_seconds", int)},
    "ratelimit": {"create_rate_per_minute": ("create_rate_per_minute", int),
                  "create_burst": ("create_burst", int),
                  "redirect_rate_per_minute": ("redirect_rate_per_minute", int),
                  "redirect_burst": ("redirect_burst", int),
                  "max_tracked_clients": ("rate_limit_max_keys", int)},
    "validation": {"blocked_domains": ("blocked_domains", list), "known_shorteners": ("known_shorteners", list)},
    "logging": {"level": ("log_level", str), "redact_keys": ("log_redact_keys", list),
                "request_sample_rate": ("log_request_sample_rate", float)},
    "cache": {"ttl_seconds": ("cache_ttl_seconds", int)},
    "analytics": {"mode": ("analytics_mode", str)},
    "http": {"trusted_proxies": ("trusted_proxies", list), "cors_allow_origins": ("cors_allow_origins", list),
             "expose_docs": ("expose_docs", bool), "hsts_max_age": ("hsts_max_age", int)},
}
SECRETS = frozenset({"admin_api_key", "ip_hash_salt", "database_url", "redis_url"})
ENV_SCHEMA: dict[str, tuple[str, type]] = {
    "URLSHORT_ENV": ("environment", str),
    "URLSHORT_DB_PATH": ("db_path", str),
    "URLSHORT_DATABASE_URL": ("database_url", str),
    "URLSHORT_REDIS_URL": ("redis_url", str),
    "URLSHORT_CACHE_TTL": ("cache_ttl_seconds", int),
    "URLSHORT_ANALYTICS_MODE": ("analytics_mode", str),
    "URLSHORT_BASE_URL": ("base_url", str),
    "URLSHORT_CODE_LENGTH": ("code_length", int),
    "URLSHORT_MAX_URL_LENGTH": ("max_url_length", int),
    "URLSHORT_MAX_TTL_SECONDS": ("max_ttl_seconds", int),
    "URLSHORT_RATE_PER_MIN": ("create_rate_per_minute", int),
    "URLSHORT_CREATE_BURST": ("create_burst", int),
    "URLSHORT_REDIRECT_RATE_PER_MIN": ("redirect_rate_per_minute", int),
    "URLSHORT_REDIRECT_BURST": ("redirect_burst", int),
    "URLSHORT_RATE_LIMIT_MAX_CLIENTS": ("rate_limit_max_keys", int),
    "URLSHORT_LOG_LEVEL": ("log_level", str),
    "URLSHORT_LOG_REDACT_KEYS": ("log_redact_keys", list),
    "URLSHORT_LOG_REQUEST_SAMPLE_RATE": ("log_request_sample_rate", float),
    "URLSHORT_TRUSTED_PROXIES": ("trusted_proxies", list),
    "URLSHORT_CORS_ALLOW_ORIGINS": ("cors_allow_origins", list),
    "URLSHORT_EXPOSE_DOCS": ("expose_docs", bool),
    "URLSHORT_HSTS_MAX_AGE": ("hsts_max_age", int),
    "URLSHORT_BLOCKED_DOMAINS": ("blocked_domains", list),
    "URLSHORT_KNOWN_SHORTENERS": ("known_shorteners", list),
    "URLSHORT_ADMIN_API_KEY": ("admin_api_key", str),
    "URLSHORT_IP_SALT": ("ip_hash_salt", str),
}
MINIMUMS = {"code_length": 4, "max_url_length": 1, "max_ttl_seconds": 1, "create_rate_per_minute": 1,
            "cache_ttl_seconds": 1, "create_burst": 1, "redirect_rate_per_minute": 1, "redirect_burst": 1,
            "rate_limit_max_keys": 1, "hsts_max_age": 0}
BOOL_WORDS = {"true": True, "1": True, "yes": True, "on": True, "false": False, "0": False, "no": False, "off": False}


def _names(items: Sequence[object], where: str) -> frozenset[str]:
    names = [i for i in items if isinstance(i, str)]
    if len(names) != len(items):
        raise ConfigError(f"{where}: expected a list of strings")
    return frozenset(n.strip().lower() for n in names if n.strip())


def _coerce(value: object, kind: type, where: str) -> object:
    if kind is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{where}: expected true or false, got {value!r}")
        return value
    if kind is int and (isinstance(value, bool) or not isinstance(value, int)):  # TOML `true` is an int in Python
        raise ConfigError(f"{where}: expected an integer, got {value!r}")
    if kind is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{where}: expected a number, got {value!r}")
        return float(value)
    if kind is str and not isinstance(value, str):
        raise ConfigError(f"{where}: expected a string, got {value!r}")
    if kind is list:
        if not isinstance(value, list):
            raise ConfigError(f"{where}: expected a list, got {value!r}")
        return _names(value, where)
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
        if kind in (int, float):
            try:
                values[name] = kind(raw)
            except ValueError as exc:
                raise ConfigError(f"{var}: expected a number, got {raw!r}") from exc
        elif kind is list:
            values[name] = _names(raw.split(","), var)
        elif kind is bool:
            if raw.strip().lower() not in BOOL_WORDS:
                raise ConfigError(f"{var}: expected true or false, got {raw!r}")
            values[name] = BOOL_WORDS[raw.strip().lower()]
        else:
            values[name] = raw
    return values


def _check(values: dict[str, object]) -> None:
    for name, minimum in MINIMUMS.items():
        value = values.get(name)
        if isinstance(value, int) and value < minimum:
            raise ConfigError(f"{name} must be at least {minimum}, got {value}")
    level = values.get("log_level")
    if isinstance(level, str):
        values["log_level"] = level.upper()
        if values["log_level"] not in LEVELS:
            raise ConfigError(f"log level must be one of {list(LEVELS)}, got {level!r}")
    rate = values.get("log_request_sample_rate")
    if isinstance(rate, float) and not 0.0 <= rate <= 1.0:
        raise ConfigError(f"request_sample_rate must be between 0 and 1, got {rate}")
    keys = values.get("log_redact_keys")
    if isinstance(keys, frozenset):
        values["log_redact_keys"] = keys | DEFAULT_REDACT_KEYS   # configured keys add to the defaults, never replace
    _check_http(values)
    mode = values.get("analytics_mode")
    if isinstance(mode, str) and mode not in ("sync", "events"):
        raise ConfigError(f"analytics mode must be 'sync' or 'events', got {mode!r}")
    environment = values.get("environment")
    if isinstance(environment, str):
        values["environment"] = environment.strip().lower()
        if values["environment"] not in ENVIRONMENTS:
            raise ConfigError(f"environment must be one of {list(ENVIRONMENTS)}, got {environment!r}")
    base_url = values.get("base_url")
    if isinstance(base_url, str):
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ConfigError(f"base_url must be an absolute http(s) URL, got {base_url!r}")
        values["base_url"] = base_url.rstrip("/")


def _check_http(values: dict[str, object]) -> None:
    proxies = values.get("trusted_proxies")
    if isinstance(proxies, frozenset):
        for cidr in proxies:
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ConfigError(f"trusted_proxies: {cidr!r} is not an IP address or CIDR range") from exc
    origins = values.get("cors_allow_origins")
    if isinstance(origins, frozenset):
        for origin in origins:
            parts = urlsplit(origin)
            exact = parts.scheme in ("http", "https") and parts.hostname and parts.path in ("", "/") \
                and not parts.query
            if origin != "*" and not exact:
                raise ConfigError(f"cors_allow_origins: {origin!r} must be '*' or a scheme://host[:port] origin")
        values["cors_allow_origins"] = frozenset(o.rstrip("/") for o in origins)


def production_problems(settings: Settings) -> list[str]:
    """Everything unsafe about running these settings in production (empty list = OK)."""
    s, problems = settings, []
    if s.ip_hash_salt == DEFAULT_IP_SALT or len(s.ip_hash_salt) < MIN_IP_SALT_LENGTH:
        problems.append(f"URLSHORT_IP_SALT must be set to a secret of at least {MIN_IP_SALT_LENGTH} characters")
    if len(s.admin_api_key) < MIN_ADMIN_KEY_LENGTH:
        problems.append(f"URLSHORT_ADMIN_API_KEY must be set (at least {MIN_ADMIN_KEY_LENGTH} characters); "
                        "without it abusive links cannot be taken down")
    if not s.base_url.startswith("https://"):
        problems.append("base_url must use https")
    if not s.database_url and s.db_path == ":memory:":
        problems.append("db_path must be a file: an in-memory database loses every link on restart")
    if s.expose_docs:
        problems.append("expose_docs must be false")
    if "*" in s.cors_allow_origins:
        problems.append("cors_allow_origins must list exact origins, not '*'")
    if s.log_level == "DEBUG":
        problems.append("log level must not be DEBUG")
    if s.hsts_max_age < 86_400:
        problems.append("hsts_max_age must be at least 86400 (one day)")
    return problems


def ensure_safe_for_environment(settings: Settings) -> None:
    """Refuse to start an unsafe production configuration, reporting every problem at once."""
    if settings.environment == "production":
        problems = production_problems(settings)
        if problems:
            raise ConfigError("refusing to start in production:\n  - " + "\n  - ".join(problems), problems)


def load_settings(path: str | Path | None = None, env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    values: dict[str, object] = {"db_path": "urlshort.db"}  # a running service persists by default
    if path:
        values.update(read_config_file(path))
    values.update(read_env(env))
    _check(values)
    known = {f.name for f in fields(Settings)}
    # Every value was type-checked against FILE_SCHEMA / ENV_SCHEMA above, so the cast is safe.
    settings = Settings(**cast(dict[str, Any], {k: v for k, v in values.items() if k in known}))
    ensure_safe_for_environment(settings)
    return settings
