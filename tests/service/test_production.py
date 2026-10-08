"""Production profile: unsafe configurations must refuse to start, listing every problem."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from urlshort.api import create_app
from urlshort.config import ConfigError, Settings, ensure_safe_for_environment, load_settings, production_problems
from urlshort.storage import SqliteRepository

SAFE = Settings(environment="production", base_url="https://sho.rt", db_path="/data/urlshort.db",
                admin_api_key="a" * 24, ip_hash_salt="s" * 16, expose_docs=False)


def test_safe_production_settings_have_no_problems() -> None:
    assert production_problems(SAFE) == []
    ensure_safe_for_environment(SAFE)


@pytest.mark.parametrize("change,fragment", [
    ({"ip_hash_salt": "change-me"}, "URLSHORT_IP_SALT"),
    ({"ip_hash_salt": "s" * 15}, "URLSHORT_IP_SALT"),
    ({"admin_api_key": ""}, "URLSHORT_ADMIN_API_KEY"),
    ({"admin_api_key": "a" * 23}, "URLSHORT_ADMIN_API_KEY"),
    ({"base_url": "http://sho.rt"}, "https"),
    ({"db_path": ":memory:"}, "in-memory"),
    ({"expose_docs": True}, "expose_docs"),
    ({"cors_allow_origins": frozenset({"*"})}, "exact origins"),
    ({"log_level": "DEBUG"}, "DEBUG"),
    ({"hsts_max_age": 0}, "hsts_max_age"),
    ({"hsts_max_age": 86_399}, "hsts_max_age"),
])
def test_each_unsafe_setting_is_reported(change: dict[str, object], fragment: str) -> None:
    problems = production_problems(replace(SAFE, **change))  # type: ignore[arg-type]
    assert len(problems) == 1 and fragment in problems[0]


def test_every_problem_is_reported_at_once() -> None:
    with pytest.raises(ConfigError) as err:
        ensure_safe_for_environment(Settings(environment="production"))
    message = str(err.value)
    assert message.startswith("refusing to start in production:")
    assert message.count("\n  - ") == 5          # salt, admin key, https, in-memory db, docs


def test_development_is_not_checked() -> None:
    ensure_safe_for_environment(Settings())         # unsafe values are fine outside production
    assert Settings().environment == "development"


def test_loading_from_environment_enforces_the_profile() -> None:
    with pytest.raises(ConfigError, match="refusing to start in production"):
        load_settings(None, {"URLSHORT_ENV": "Production"})
    ok = load_settings(None, {"URLSHORT_ENV": "production", "URLSHORT_BASE_URL": "https://sho.rt",
                              "URLSHORT_ADMIN_API_KEY": "a" * 24, "URLSHORT_IP_SALT": "s" * 16,
                              "URLSHORT_EXPOSE_DOCS": "false"})
    assert ok.environment == "production"


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ConfigError, match="environment must be one of"):
        load_settings(None, {"URLSHORT_ENV": "prod"})


def test_settings_built_in_code_cannot_bypass_the_check() -> None:
    with pytest.raises(ConfigError, match="refusing to start in production"):
        create_app(Settings(environment="production"), SqliteRepository())


def test_safe_production_app_serves_traffic_without_docs() -> None:
    client = TestClient(create_app(SAFE, SqliteRepository()))
    assert client.get("/livez").status_code == 200
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404
    assert client.get("/livez").headers["strict-transport-security"].startswith("max-age=31536000")
    assert client.post("/api/v1/links", json={"url": "https://example.com/p"}).json()["short_url"].startswith("https://")


def test_summary_reports_environment() -> None:
    assert SAFE.summary()["environment"] == "production"


def test_postgres_satisfies_the_persistent_storage_check() -> None:
    assert production_problems(replace(SAFE, db_path=":memory:", database_url="postgresql://u:p@db/x")) == []
