"""HTTP adapter. Built through an app factory so every test gets a fresh, isolated app."""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="URL Shortener", version=__version__)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Liveness: the process is up and serving requests."""
        return {"status": "ok", "version": __version__}

    return app
