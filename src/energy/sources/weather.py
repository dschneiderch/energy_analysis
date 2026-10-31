"""Hourly outdoor temperature and wind from Open-Meteo (archive, then recent forecast)."""
from datetime import date, datetime, timedelta, timezone

from .. import config, http, store

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_START = date(2021, 11, 1)
_FMT = "%Y-%m-%dT%H:%M:%SZ"


def parse_open_meteo(payload: dict, source: str) -> list[dict]:
    h = payload["hourly"]
    rows = []
    for t, temp, wind in zip(h["time"], h["temperature_2m"], h["wind_speed_10m"]):
        if temp is None:
            continue
        ts = datetime.strptime(t, "%Y-%m-%dT%H:%M").strftime(_FMT)
        rows.append({"ts": ts, "temp_f": temp, "wind_mph": wind, "source": source})
    return rows


PROVISIONAL_DAYS = 5


def tag_provisional(rows: list[dict], now: datetime) -> list[dict]:
    """The archive endpoint serves modelled (forecast-like) values for the last few days;
    tag those 'forecast' so reports note them. A later sync re-fetches them as 'archive'."""
    cutoff = (now - timedelta(days=PROVISIONAL_DAYS)).strftime(_FMT)
    return [{**r, "source": "forecast"} if r["source"] == "archive" and r["ts"] > cutoff else r
            for r in rows]


def _params(**extra) -> dict:
    return {"latitude": config.LATITUDE, "longitude": config.LONGITUDE,
            "hourly": "temperature_2m,wind_speed_10m", "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph", "timezone": "UTC", **extra}


def _get(url: str, params: dict) -> dict:
    resp = http.request("GET", url, params=params)
    http.raise_for_status(resp)
    return resp.json()


def sync(conn, since: date | None = None) -> int:
    if since:
        start = since
    else:
        latest = conn.execute("SELECT max(ts) FROM weather_hourly WHERE source='archive'").fetchone()[0]
        start = (datetime.strptime(latest, _FMT).date() - timedelta(days=7)) if latest else DEFAULT_START
    today = datetime.now(timezone.utc).date()
    total = 0
    cur = start
    while cur <= today:
        end = min(cur + timedelta(days=364), today)
        payload = _get(ARCHIVE_URL, _params(start_date=cur.isoformat(), end_date=end.isoformat()))
        rows = tag_provisional(parse_open_meteo(payload, "archive"), datetime.now(timezone.utc))
        n = store.upsert(conn, "weather_hourly", rows)
        store.log_sync(conn, "weather_archive", cur.isoformat(), end.isoformat(), n, "ok")
        total += n
        cur = end + timedelta(days=1)
    # Forecast API fills the recent days the archive does not have yet; never overrides archive rows.
    payload = _get(FORECAST_URL, _params(past_days=10, forecast_days=1))
    have = {r[0] for r in conn.execute("SELECT ts FROM weather_hourly WHERE source='archive'")}
    rows = [r for r in parse_open_meteo(payload, "forecast") if r["ts"] not in have]
    n = store.upsert(conn, "weather_hourly", rows)
    store.log_sync(conn, "weather_forecast", "past_10d", "today", n, "ok")
    return total + n
