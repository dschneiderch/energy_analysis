import time
from datetime import date

import requests

from energy import http, store


class FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_schema_has_all_tables(db):
    names = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    assert {"ecobee_interval","ecobee_sensor","ecobee_daily","emporia_hourly","grid_carbon_hourly",
            "weather_hourly","gas_daily","rates","not_home","sync_log"} <= names

def test_upsert_is_idempotent(db):
    row = {"channel": "7", "ts": "2026-01-15T08:00:00Z", "kwh": 1.5, "channel_name": "heatpump"}
    assert store.upsert(db, "emporia_hourly", [row]) == 1
    store.upsert(db, "emporia_hourly", [{**row, "kwh": 2.0}])
    assert tuple(db.execute("select count(*), max(kwh) from emporia_hourly").fetchone()) == (1, 2.0)

def test_local_day_bounds_dst():
    assert store.local_day_bounds(date(2025, 11, 2)) == ("2025-11-02T07:00:00Z", "2025-11-03T08:00:00Z")
    assert store.local_day_bounds(date(2026, 1, 15)) == ("2026-01-15T08:00:00Z", "2026-01-16T08:00:00Z")

def test_http_retries_5xx_then_returns(monkeypatch):
    responses = [FakeResp(503), FakeResp(200)]
    monkeypatch.setattr(requests, "request", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    assert http.request("GET", "https://x").status_code == 200 and responses == []
