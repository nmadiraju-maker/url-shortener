"""Change plan: clicks_by_hour (UTC) in link stats."""
KIND, SCOPE = "feat", "analytics"

EDITS = [{'file': 'urlshort/analytics.py',
  'find': '    by_day = Counter(c.ts.astimezone(UTC).date().isoformat() for c in human)\n',
  'replace': '    by_day = Counter(c.ts.astimezone(UTC).date().isoformat() for c in human)\n'
             '    by_hour = Counter(f"{c.ts.astimezone(UTC).hour:02d}" for c in human)\n'},
 {'file': 'urlshort/analytics.py',
  'find': '        "clicks_by_day": dict(sorted(by_day.items())),\n',
  'replace': '        "clicks_by_day": dict(sorted(by_day.items())),\n'
             '        "clicks_by_hour": dict(sorted(by_hour.items())),\n'},
 {'file': 'urlshort/models.py',
  'find': '    clicks_by_day: dict[str, int]\n',
  'replace': '    clicks_by_day: dict[str, int]\n'
             '    clicks_by_hour: dict[str, int]          # UTC hour "00"-"23" -> human clicks\n'},
 {'file': 'tests/service/test_analytics.py',
  'find': '    assert summarise([]) == {"total_clicks": 0, "bot_clicks": 0, "clicks_by_day": {}, '
          '"unique_visitors_by_day": {},\n'
          '                             "top_referrers": [], "agents": {}, "last_click_at": None}',
  'replace': '    assert summarise([]) == {"total_clicks": 0, "bot_clicks": 0, "clicks_by_day": {}, '
             '"clicks_by_hour": {},\n'
             '                             "unique_visitors_by_day": {}, "top_referrers": [], "agents": {}, '
             '"last_click_at": None}'},
 {'file': 'tests/service/test_hourly.py',
  'create': '"""Clicks by hour of day, UTC (AC-HOURLY-1)."""\n'
            'from datetime import timedelta, timezone\n'
            '\n'
            'from fastapi.testclient import TestClient\n'
            '\n'
            'from urlshort.analytics import summarise\n'
            'from urlshort.storage import Click\n'
            '\n'
            'from .helpers import FakeClock\n'
            '\n'
            'CHROME = {"user-agent": "Mozilla/5.0 Chrome/126.0"}\n'
            '\n'
            '\n'
            'def test_stats_include_clicks_by_hour(client: TestClient, clock: FakeClock) -> None:\n'
            '    """AC-HOURLY-1"""\n'
            '    link = client.post("/api/v1/links", json={"url": "https://example.com/h"}).json()\n'
            '    client.get(f"/{link[\'code\']}", headers=CHROME, follow_redirects=False)\n'
            '    clock.advance(3 * 3600)\n'
            '    client.get(f"/{link[\'code\']}", headers=CHROME, follow_redirects=False)\n'
            '    client.get(f"/{link[\'code\']}", headers={"user-agent": "Googlebot/2.1"}, follow_redirects=False)\n'
            '    stats = client.get(f"/api/v1/links/{link[\'code\']}/stats", headers={"x-stats-token": '
            'link["stats_token"]}).json()\n'
            '    assert stats["clicks_by_hour"] == {"12": 1, "15": 1}                     # bots excluded\n'
            '\n'
            '\n'
            'def test_hours_are_utc_whatever_the_click_timezone(clock: FakeClock) -> None:\n'
            '    """AC-HOURLY-1"""\n'
            '    ist = clock().astimezone(timezone(timedelta(hours=5, minutes=30)))       # 17:30 in India = 12:00 '
            'UTC\n'
            '    click = Click(code="c", ts=ist, referrer_host=None, agent_family="chrome", is_bot=False, '
            'ip_id=None,\n'
            '                  ip_key_id=None)\n'
            '    assert summarise([click])["clicks_by_hour"] == {"12": 1}\n'}]
