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
    ("[validation]\nblocked_domains = [1]\n", "list of domain strings"),
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
