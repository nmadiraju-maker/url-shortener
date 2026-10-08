"""API keys, roles and per-caller identity (owner and admin keys), and secrets from files."""
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from urlshort.api import create_app
from urlshort.auth import BOOTSTRAP_ADMIN, authenticate, create_key
from urlshort.config import ConfigError, Settings, load_settings
from urlshort.errors import InvalidInput
from urlshort.storage import SqliteRepository

from .helpers import FakeClock


@pytest.fixture
def keys(repo: SqliteRepository, clock: FakeClock) -> dict[str, str]:
    return {"team-a": create_key(repo, name="team-a ci", role="owner", owner="team-a", now=clock()),
            "team-b": create_key(repo, name="team-b ci", role="owner", owner="team-b", now=clock()),
            "alice": create_key(repo, name="alice", role="admin", owner="ignored", now=clock())}


@pytest.fixture
def app(repo: SqliteRepository, clock: FakeClock) -> TestClient:
    return TestClient(create_app(Settings(admin_api_key="break-glass-key"), repo, clock=clock))


def bearer(key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {key}"}


def make(app: TestClient, url: str, key: str) -> str:
    return str(app.post("/api/v1/links", json={"url": url}, headers=bearer(key)).json()["code"])


# ---------------------------------------------------------------- keys
def test_keys_are_shown_once_and_stored_hashed(repo: SqliteRepository, keys: dict[str, str]) -> None:
    stored = repo.list_api_keys()
    assert len(stored) == 3 and all(k.startswith("us_") and len(k) == 59 for k in keys.values())
    assert not any(secret.split("_", 2)[2] in str(stored) for secret in keys.values())
    admin = next(k for k in stored if k.name == "alice")
    assert (admin.role, admin.owner) == ("admin", None)                    # admins own nothing


@pytest.mark.parametrize("kwargs,message", [({"name": "x", "role": "root", "owner": None}, "role"),
                                            ({"name": "x", "role": "owner", "owner": None}, "needs an owner"),
                                            ({"name": " ", "role": "admin", "owner": None}, "needs a name")])
def test_key_creation_is_validated(repo: SqliteRepository, clock: FakeClock, kwargs: dict, message: str) -> None:
    with pytest.raises(InvalidInput, match=message):
        create_key(repo, now=clock(), **kwargs)


def test_authentication(repo: SqliteRepository, keys: dict[str, str], clock: FakeClock) -> None:
    owner = authenticate(repo, keys["team-a"])
    assert owner is not None and (owner.actor, owner.owner) == ("owner:team-a ci", "team-a")
    assert authenticate(repo, "break-glass-key", bootstrap_admin_key="break-glass-key") is BOOTSTRAP_ADMIN
    wrong_secret = keys["team-a"][:-1] + ("A" if keys["team-a"][-1] != "A" else "B")
    unknown = "us_000000000000_" + keys["team-a"].split("_", 2)[2]
    for bad in (None, "", "not-a-key", wrong_secret, unknown):
        assert authenticate(repo, bad) is None
    key_id = keys["team-a"].split("_")[1]
    assert repo.revoke_api_key(key_id, clock() + timedelta(hours=1)) is True
    assert repo.revoke_api_key(key_id, clock()) is False and authenticate(repo, keys["team-a"]) is None


# ---------------------------------------------------------------- owners
def test_owner_keys_create_as_their_owner_and_read_their_own_stats(app: TestClient, keys: dict[str, str]) -> None:
    made = app.post("/api/v1/links", json={"url": "https://example.com/a"},
                    headers={**bearer(keys["team-a"]), "x-owner": "team-b"}).json()   # X-Owner ignored
    stats = f"/api/v1/links/{made['code']}/stats"
    assert app.get(stats, headers=bearer(keys["team-a"])).status_code == 200           # no stats token needed
    assert app.get(stats, headers={"x-api-key": keys["team-a"]}).status_code == 200    # either header
    assert app.get(stats, headers=bearer(keys["team-b"])).status_code == 401           # not theirs
    assert app.get("/api/v1/links/nope/stats", headers=bearer(keys["team-a"])).status_code == 401
    assert app.get(stats, headers={"x-stats-token": made["stats_token"]}).status_code == 200   # tokens still work


def test_owners_take_down_only_their_own_links(app: TestClient, keys: dict[str, str], repo: SqliteRepository) -> None:
    code = make(app, "https://example.com/t", keys["team-a"])
    assert app.delete(f"/api/v1/links/{code}", headers=bearer(keys["team-b"])).status_code == 404   # looks absent
    assert app.delete(f"/api/v1/links/{code}").status_code == 401
    assert app.delete(f"/api/v1/links/{code}", headers=bearer(keys["team-a"])).status_code == 204
    assert repo.audit_records()[-1].actor == "owner:team-a ci"


def test_admins_act_under_their_own_name(app: TestClient, keys: dict[str, str], repo: SqliteRepository) -> None:
    code = make(app, "https://example.com/x", keys["team-b"])
    assert app.get(f"/api/v1/links/{code}/stats", headers=bearer(keys["alice"])).status_code == 200
    assert app.get("/api/v1/links/nope/stats", headers=bearer(keys["alice"])).status_code == 404
    assert app.delete(f"/api/v1/links/{code}", headers=bearer(keys["alice"])).status_code == 204
    assert repo.audit_records()[-1].actor == "admin:alice"
    other = app.post("/api/v1/links", json={"url": "https://example.com/y"}).json()["code"]
    assert app.delete(f"/api/v1/links/{other}", headers={"x-api-key": "break-glass-key"}).status_code == 204
    assert repo.audit_records()[-1].actor == "admin:bootstrap"


def test_invalid_or_revoked_keys_are_rejected_not_ignored(app: TestClient, keys: dict[str, str],
                                                          repo: SqliteRepository, clock: FakeClock) -> None:
    repo.revoke_api_key(keys["team-a"].split("_")[1], clock())
    for i, headers in enumerate((bearer(keys["team-a"]), {"x-api-key": "us_garbage"})):
        resp = app.post("/api/v1/links", json={"url": f"https://example.com/r{i}"}, headers=headers)
        assert resp.status_code == 401 and resp.json()["error"]["message"] == "invalid or revoked API key"
    for i, headers in enumerate(({"authorization": "Bearer "}, {"authorization": "Basic abc"})):   # no key sent
        assert app.post("/api/v1/links", json={"url": f"https://example.com/a{i}"}, headers=headers).status_code == 201


def test_api_keys_can_be_required_for_creation(repo: SqliteRepository, clock: FakeClock, keys: dict[str, str]) -> None:
    strict = TestClient(create_app(Settings(require_api_key=True), repo, clock=clock))
    assert strict.post("/api/v1/links", json={"url": "https://example.com/s"}).status_code == 401
    assert strict.post("/api/v1/links", json={"url": "https://example.com/s"},
                       headers=bearer(keys["team-a"])).status_code == 201
    assert load_settings(None, {"URLSHORT_REQUIRE_API_KEY": "true"}).require_api_key is True


# ---------------------------------------------------------------- secrets from files
def test_secrets_can_come_from_files(tmp_path: Path) -> None:
    secret = tmp_path / "admin_key"
    secret.write_text("from-a-file\n")
    s = load_settings(None, {"URLSHORT_ADMIN_API_KEY_FILE": str(secret), "URLSHORT_AUDIT_ANCHOR_KEY": "a"})
    assert s.admin_api_key == "from-a-file" and s.audit_anchor_key == "a"
    with pytest.raises(ConfigError, match="not both"):
        load_settings(None, {"URLSHORT_ADMIN_API_KEY_FILE": str(secret), "URLSHORT_ADMIN_API_KEY": "x"})
    with pytest.raises(ConfigError, match="cannot read"):
        load_settings(None, {"URLSHORT_ADMIN_API_KEY_FILE": str(tmp_path / "missing")})
    assert load_settings(None, {"URLSHORT_DB_PATH_FILE": str(secret)}).db_path != "from-a-file"   # secrets only
