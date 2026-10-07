"""The process entry point used by uvicorn and the Docker image."""
import json
from pathlib import Path

import pytest
from fastapi import FastAPI

from urlshort.logging_setup import shutdown_logging
from urlshort.main import EXIT_CONFIG_ERROR, app_factory


def test_valid_configuration_returns_the_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("URLSHORT_CONFIG", raising=False)
    monkeypatch.setenv("URLSHORT_DB_PATH", str(tmp_path / "links.db"))
    assert isinstance(app_factory(), FastAPI)


@pytest.mark.parametrize("env,expected_problem", [
    ({"URLSHORT_ENV": "production"}, "base_url must use https"),          # unsafe production settings
    ({"URLSHORT_CODE_LENGTH": "2"}, "code_length must be at least 4"),    # invalid settings in any environment
])
def test_invalid_configuration_exits_with_one_json_line(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], env: dict[str, str],
        expected_problem: str) -> None:
    monkeypatch.delenv("URLSHORT_CONFIG", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    shutdown_logging()                                # so the entry point logs to the captured stderr
    with pytest.raises(SystemExit) as exit_info:
        app_factory()
    assert exit_info.value.code == EXIT_CONFIG_ERROR
    lines = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x.startswith("{")]
    (critical,) = [x for x in lines if x["level"] == "CRITICAL"]
    assert critical["msg"] == "refusing to start: invalid configuration"
    assert any(expected_problem in p for p in critical["ctx"]["problems"])
