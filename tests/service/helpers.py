"""Shared test helpers. (conftest.py is for fixtures and is loaded by pytest; it should not be imported.)"""

from datetime import UTC, datetime, timedelta


class FakeClock:
    """Controllable time, so expiry tests don't sleep."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
