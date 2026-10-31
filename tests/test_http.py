import time
from datetime import date

import pytest
import requests

from energy import http, store
from energy.sources.emporia import start_for_channel

SECRET = "SECRET123"
URL = f"https://api.example.com/path?api_key={SECRET}&x=1"


class Resp:
    def __init__(self, code):
        self.status_code = code
        self.request = requests.Request("GET", URL).prepare()


@pytest.fixture(autouse=True)
def nosleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


def _chain(exc):
    while exc:
        yield exc
        exc = exc.__cause__ or exc.__context__


def test_exhausted_5xx_error_has_no_secret(monkeypatch):
    monkeypatch.setattr(requests, "request", lambda *a, **k: Resp(500))
    with pytest.raises(http.HttpError) as ei:
        http.request("GET", URL)
    assert "api_key" not in str(ei.value) and SECRET not in str(ei.value)
    assert "api.example.com/path" in str(ei.value) and "500" in str(ei.value)


def test_final_connection_error_has_no_secret(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError(f"Max retries exceeded with url: /path?api_key={SECRET}")
    monkeypatch.setattr(requests, "request", boom)
    with pytest.raises(http.HttpError) as ei:
        http.request("GET", URL)
    assert all(SECRET not in str(e) for e in _chain(ei.value))


def test_4xx_returned_without_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "request", lambda *a, **k: calls.append(1) or Resp(404))
    assert http.request("GET", URL).status_code == 404 and len(calls) == 1


def test_raise_for_status_redacts():
    with pytest.raises(http.HttpError) as ei:
        http.raise_for_status(Resp(404))
    assert SECRET not in str(ei.value) and all(SECRET not in str(e) for e in _chain(ei.value))
    http.raise_for_status(Resp(200))


def test_emporia_start_per_channel(db):
    store.upsert(db, "emporia_hourly", [
        {"channel": "7", "ts": "2026-01-15T08:00:00Z", "kwh": 1.0, "channel_name": "a"},
        {"channel": "8", "ts": "2025-06-10T08:00:00Z", "kwh": 1.0, "channel_name": "b"}])
    assert start_for_channel(db, "7", None).date() == date(2026, 1, 13)
    assert start_for_channel(db, "8", None).date() == date(2025, 6, 8)
    assert start_for_channel(db, "new", None).date() == date(2023, 4, 15)
    assert start_for_channel(db, "7", date(2024, 1, 1)).date() == date(2024, 1, 1)
