"""Clicks by hour of day, UTC (AC-HOURLY-1)."""
from datetime import timedelta, timezone

from fastapi.testclient import TestClient

from urlshort.analytics import summarise
from urlshort.storage import Click

from .helpers import FakeClock

CHROME = {"user-agent": "Mozilla/5.0 Chrome/126.0"}


def test_stats_include_clicks_by_hour(client: TestClient, clock: FakeClock) -> None:
    """AC-HOURLY-1"""
    link = client.post("/api/v1/links", json={"url": "https://example.com/h"}).json()
    client.get(f"/{link['code']}", headers=CHROME, follow_redirects=False)
    clock.advance(3 * 3600)
    client.get(f"/{link['code']}", headers=CHROME, follow_redirects=False)
    client.get(f"/{link['code']}", headers={"user-agent": "Googlebot/2.1"}, follow_redirects=False)
    stats = client.get(f"/api/v1/links/{link['code']}/stats", headers={"x-stats-token": link["stats_token"]}).json()
    assert stats["clicks_by_hour"] == {"12": 1, "15": 1}                     # bots excluded


def test_hours_are_utc_whatever_the_click_timezone(clock: FakeClock) -> None:
    """AC-HOURLY-1"""
    ist = clock().astimezone(timezone(timedelta(hours=5, minutes=30)))       # 17:30 in India = 12:00 UTC
    click = Click(code="c", ts=ist, referrer_host=None, agent_family="chrome", is_bot=False, ip_id=None,
                  ip_key_id=None)
    assert summarise([click])["clicks_by_hour"] == {"12": 1}
