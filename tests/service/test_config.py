from pathlib import Path

import pytest

from urlshort.config import ConfigError, Settings, load_settings
from urlshort.validation import KNOWN_SHORTENERS

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "urlshort.example.toml"


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "c.toml"
    path.write_text(body)
    return path


def test_defaults_without_file_or_env() -> None:
    s = load_settings(None, {})
    assert (s.db_path, s.code_length, s.base_url) == ("urlshort.db", 7, "http://localhost:8000")
    assert s.known_shorteners == KNOWN_SHORTENERS and s.blocked_domains == frozenset()


def test_example_config_loads() -> None:
    s = load_settings(EXAMPLE, {})
    assert s.code_length == 7 and s.known_shorteners == KNOWN_SHORTENERS


def test_precedence_defaults_then_file_then_env(tmp_path: Path) -> None:
    path = write(tmp_path, '[service]\nbase_url = "https://sho.rt/"\ncode_length = 9\n'
                           '[ratelimit]\ncreate_rate_per_minute = 5\n'
                           '[validation]\nblocked_domains = ["Evil.Example", " "]\n'
                           'known_shorteners = ["short.example"]\n')
    s = load_settings(path, {"URLSHORT_CODE_LENGTH": "11", "URLSHORT_ADMIN_API_KEY": "k",
                             "URLSHORT_KNOWN_SHORTENERS": "a.example, b.example,"})
    assert s.base_url == "https://sho.rt"                       # trailing slash removed
    assert s.code_length == 11                                  # env beats file
    assert s.create_rate_per_minute == 5                        # file beats default
    assert s.blocked_domains == {"evil.example"}                # lower-cased, blanks dropped
    assert s.known_shorteners == {"a.example", "b.example"}
    assert s.admin_api_key == "k" and s.max_url_length == 2048


def test_from_env_reads_config_path(tmp_path: Path) -> None:
    path = write(tmp_path, "[service]\ncode_length = 8\n")
    assert Settings.from_env({"URLSHORT_CONFIG": str(path)}).code_length == 8


def test_from_env_uses_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("URLSHORT_CONFIG", raising=False)
    monkeypatch.setenv("URLSHORT_RATE_PER_MIN", "7")
    assert Settings.from_env().create_rate_per_minute == 7
    assert load_settings().create_rate_per_minute == 7


@pytest.mark.parametrize("body,message", [
    ("[servce]\nx = 1\n", "unknown section"),
    ("service = 1\n", "unknown section"),
    ("[service]\ncodelength = 7\n", "unknown key"),
    ("[service]\ncode_length = true\n", "expected an integer"),
    ("[service]\ncode_length = \"7\"\n", "expected an integer"),
    ("[service]\nbase_url = 5\n", "expected a string"),
    ("[validation]\nblocked_domains = \"x.com\"\n", "expected a list"),
    ("[validation]\nblocked_domains = [1]\n", "list of strings"),
    ("[service]\nadmin_api_key = \"oops\"\n", "secret"),
    ("[service]\ncode_length = 3\n", "at least 4"),
    ("[service]\nmax_ttl_seconds = 0\n", "at least 1"),
    ("[service]\nbase_url = \"oops\"\n", "absolute http"),
    ("[service]\nbase_url = \"ftp://x.example\"\n", "absolute http"),
    ("[service\n", "invalid TOML"),
])
def test_invalid_config_fails_fast(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_settings(write(tmp_path, body), {})


def test_missing_file_and_bad_env_values(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "missing.toml", {})
    with pytest.raises(ConfigError, match="URLSHORT_RATE_PER_MIN"):
        load_settings(None, {"URLSHORT_RATE_PER_MIN": "fast"})
    with pytest.raises(ConfigError, match="at least 4"):
        load_settings(None, {"URLSHORT_CODE_LENGTH": "2"})


def test_rate_limit_settings(tmp_path: Path) -> None:
    path = write(tmp_path, "[ratelimit]\ncreate_burst = 5\nredirect_rate_per_minute = 120\nmax_tracked_clients = 500\n")
    s = load_settings(path, {"URLSHORT_REDIRECT_BURST": "20"})
    assert (s.create_burst, s.redirect_rate_per_minute, s.redirect_burst, s.rate_limit_max_keys) == (5, 120, 20, 500)
    assert s.create_rate_per_minute == 60                       # untouched default
    with pytest.raises(ConfigError, match="create_burst must be at least 1"):
        load_settings(write(tmp_path, "[ratelimit]\ncreate_burst = 0\n"), {})


def test_logging_settings(tmp_path: Path) -> None:
    from urlshort.logging_setup import DEFAULT_REDACT_KEYS
    path = write(tmp_path, '[logging]\nlevel = "debug"\nredact_keys = ["X-Session"]\nrequest_sample_rate = 0.25\n')
    s = load_settings(path, {})
    assert s.log_level == "DEBUG" and s.log_request_sample_rate == 0.25
    assert s.log_redact_keys == DEFAULT_REDACT_KEYS | {"x-session"}           # added to, never replacing, defaults
    assert load_settings(None, {"URLSHORT_LOG_REQUEST_SAMPLE_RATE": "1"}).log_request_sample_rate == 1.0
    assert load_settings(write(tmp_path, "[logging]\nrequest_sample_rate = 0\n"), {}).log_request_sample_rate == 0.0


@pytest.mark.parametrize("body,message", [
    ('[logging]\nlevel = "LOUD"\n', "log level must be one of"),
    ("[logging]\nrequest_sample_rate = 1.5\n", "between 0 and 1"),
    ("[logging]\nrequest_sample_rate = true\n", "expected a number"),
    ('[logging]\nrequest_sample_rate = "half"\n', "expected a number"),
])
def test_invalid_logging_settings(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_settings(write(tmp_path, body), {})


def test_bad_float_in_environment() -> None:
    with pytest.raises(ConfigError, match="URLSHORT_LOG_REQUEST_SAMPLE_RATE"):
        load_settings(None, {"URLSHORT_LOG_REQUEST_SAMPLE_RATE": "half"})


def test_summary_masks_secrets() -> None:
    summary = Settings(admin_api_key="k3y", ip_hash_salt="s4lt", blocked_domains=frozenset({"b", "a"})).summary()
    assert summary["admin_api_key"] == "set" and summary["ip_hash_salt"] == "set"
    assert "k3y" not in str(summary) and "s4lt" not in str(summary)
    assert summary["blocked_domains"] == ["a", "b"]
    unset = Settings().summary()
    assert unset["admin_api_key"] == "NOT SET" and unset["ip_hash_salt"] == "NOT SET"   # default salt = not set


def test_http_settings(tmp_path: Path) -> None:
    path = write(tmp_path, '[http]\ntrusted_proxies = ["10.0.0.0/8", "192.168.1.10"]\n'
                           'cors_allow_origins = ["https://App.example/", "*"]\n'
                           'expose_docs = false\nhsts_max_age = 0\n')
    s = load_settings(path, {})
    assert s.trusted_proxies == {"10.0.0.0/8", "192.168.1.10"} and s.expose_docs is False and s.hsts_max_age == 0
    assert s.cors_allow_origins == {"https://app.example", "*"}
    env = load_settings(None, {"URLSHORT_EXPOSE_DOCS": "No", "URLSHORT_TRUSTED_PROXIES": "fd00::/8"})
    assert env.expose_docs is False and env.trusted_proxies == {"fd00::/8"}
    assert load_settings(None, {"URLSHORT_EXPOSE_DOCS": "on"}).expose_docs is True


@pytest.mark.parametrize("body,message", [
    ('[http]\ntrusted_proxies = ["10.0.0.0/33"]\n', "not an IP address or CIDR"),
    ('[http]\ntrusted_proxies = ["my-proxy.internal"]\n', "not an IP address or CIDR"),
    ('[http]\ncors_allow_origins = ["https://app.example/path"]\n', "scheme://host"),
    ('[http]\ncors_allow_origins = ["ftp://app.example"]\n', "scheme://host"),
    ('[http]\ncors_allow_origins = ["app.example"]\n', "scheme://host"),
    ('[http]\ncors_allow_origins = ["https://app.example/?x=1"]\n', "scheme://host"),
    ("[http]\nexpose_docs = 1\n", "expected true or false"),
    ("[http]\nhsts_max_age = -1\n", "at least 0"),
])
def test_invalid_http_settings(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_settings(write(tmp_path, body), {})


def test_bad_boolean_in_environment() -> None:
    with pytest.raises(ConfigError, match="URLSHORT_EXPOSE_DOCS"):
        load_settings(None, {"URLSHORT_EXPOSE_DOCS": "maybe"})


def test_infrastructure_urls_are_env_only_secrets(tmp_path: Path) -> None:
    s = load_settings(None, {"URLSHORT_DATABASE_URL": "postgresql://u:pw@db/urlshort",
                             "URLSHORT_REDIS_URL": "redis://:pw@cache:6379/0", "URLSHORT_CACHE_TTL": "30"})
    assert (s.database_url, s.redis_url, s.cache_ttl_seconds) == ("postgresql://u:pw@db/urlshort",
                                                                  "redis://:pw@cache:6379/0", 30)
    summary = s.summary()
    assert summary["database_url"] == "set" and summary["redis_url"] == "set" and "pw" not in str(summary)
    assert load_settings(write(tmp_path, "[cache]\nttl_seconds = 5\n"), {}).cache_ttl_seconds == 5
    with pytest.raises(ConfigError):                                    # secrets may not live in the file
        load_settings(write(tmp_path, '[service]\ndatabase_url = "postgresql://u:pw@db/x"\n'), {})
