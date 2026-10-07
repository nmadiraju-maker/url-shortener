from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from urlshort import __version__
from urlshort.api import create_app
from urlshort.storage import SqliteRepository


def test_healthz_reports_ok_and_version(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}


def test_readyz_reflects_database_reachability(client: TestClient, repo: SqliteRepository) -> None:
    assert client.get("/readyz").json() == {"status": "ready"}
    repo.close()
    resp = client.get("/readyz")
    assert resp.status_code == 503 and resp.json() == {"status": "degraded"}


def test_default_app_reads_config_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "links.db"
    monkeypatch.setenv("URLSHORT_DB_PATH", str(db))
    monkeypatch.delenv("URLSHORT_CONFIG", raising=False)
    first, second = create_app(), create_app()
    assert first is not second
    assert TestClient(first).get("/healthz").status_code == 200 and db.exists()
