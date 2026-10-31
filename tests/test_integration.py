"""Live, read-only checks against every external source (spec §10). Run with `pytest -m integration`."""
import asyncio
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import pytest

from energy import config, store
from energy.sources import beestat, ecobee, emporia, ha_carbon, weather

pytestmark = pytest.mark.integration

DAY = date.today() - timedelta(days=3)
_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _bounds():
    return tuple(datetime.strptime(s, _FMT).replace(tzinfo=timezone.utc) for s in store.local_day_bounds(DAY))


def test_ecobee_288_intervals_per_thermostat():
    payload = ecobee._fetch(ecobee.access_token(), DAY - timedelta(days=1), DAY + timedelta(days=1))
    intervals, _ = ecobee.parse_runtime_report(payload)
    start, end = store.local_day_bounds(DAY)
    counts = Counter(r["thermostat_id"] for r in intervals if start <= r["ts"] < end)
    expected = len(store.local_day_hours(DAY)) * 12
    assert counts[config.LR_ECOBEE_ID] == expected
    assert counts[config.UP_ECOBEE_ID] == expected


def test_beestat_summary_rows():
    key = config.load_secrets()["BEESTAT_API_KEY"]
    from energy import http
    resp = http.request("GET", beestat.URL, params={"api_key": key, "resource": "runtime_thermostat_summary",
                                                    "method": "read_id"},
                        headers={"User-Agent": beestat.USER_AGENT})
    http.raise_for_status(resp)
    data = resp.json()["data"]
    rows = beestat.parse_summaries(list(data.values()) if isinstance(data, dict) else list(data))
    have = {str(r["thermostat_id"]) for r in rows if r["date"] == DAY.isoformat()}
    assert {str(config.BEESTAT_UP), str(config.BEESTAT_LR)} <= have


def test_emporia_hourly_channels():
    vue = emporia._login()
    chans = {num: ch for num, _, ch in emporia._channels(vue)}
    start, end = _bounds()
    from pyemvue.enums import Scale, Unit
    sums = {}
    for num in ("7", "8", config.MAINS_CHANNEL, "97"):
        usage, first = vue.get_chart_usage(chans[num], start, end, scale=Scale.HOUR.value, unit=Unit.KWH.value)
        rows = emporia.rows_from_chart(num, num, usage or [], first, end)
        if num != "97":
            assert len(rows) == len(store.local_day_hours(DAY)), num
        sums[num] = sum(r["kwh"] for r in rows)
    assert sums["7"] + sums["8"] == pytest.approx(sums["97"], rel=0.02, abs=0.05)


def test_ha_carbon_hours():
    start, end = _bounds()
    token = config.load_secrets()["HA_TOKEN"]
    (result,) = asyncio.run(ha_carbon._fetch(token, [(start, end)]))
    rows = [r for r in ha_carbon.parse_statistics(result) if r["co2_g_per_kwh"] is not None]
    assert len(rows) >= 20


def test_open_meteo_24_hours():
    payload = weather._get(weather.FORECAST_URL, weather._params(past_days=10, forecast_days=1))
    start, end = store.local_day_bounds(DAY)
    rows = [r for r in weather.parse_open_meteo(payload, "forecast") if start <= r["ts"] < end]
    assert len(rows) == len(store.local_day_hours(DAY))
    assert all(-40 < r["temp_f"] < 120 for r in rows)
