from fastapi.testclient import TestClient

from urlshort import __version__
from urlshort.api import create_app


def test_healthz_reports_ok_and_version() -> None:
    resp = TestClient(create_app()).get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}


def test_each_app_is_independent() -> None:
    assert create_app() is not create_app()
