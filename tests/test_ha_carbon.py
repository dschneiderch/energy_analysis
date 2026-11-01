from energy.sources.ha_carbon import parse_statistics


def test_parse_statistics_joins_both_ids():
    res = {"sensor.electricity_maps_co2_intensity": [{"start": 1768464000000, "mean": 234.0}],
           "sensor.electricity_maps_grid_fossil_fuel_percentage": [{"start": 1768464000000, "mean": 37.5}]}
    assert parse_statistics(res) == [{"ts": "2026-01-15T08:00:00Z", "co2_g_per_kwh": 234.0, "fossil_pct": 37.5}]


def test_parse_statistics_missing_value_is_none():
    res = {"sensor.electricity_maps_co2_intensity": [{"start": 1768464000000, "mean": None}],
           "sensor.electricity_maps_grid_fossil_fuel_percentage": [{"start": 1768467600000, "mean": 10.0}]}
    assert parse_statistics(res) == [
        {"ts": "2026-01-15T08:00:00Z", "co2_g_per_kwh": None, "fossil_pct": None},
        {"ts": "2026-01-15T09:00:00Z", "co2_g_per_kwh": None, "fossil_pct": 10.0}]


def test_sync_archives_then_fresh_db_rebuilds_from_archive(db, tmp_path, monkeypatch):
    from datetime import date
    from energy import config, store
    from energy.sources import ha_carbon
    monkeypatch.setattr(config, "HA_CARBON_DIR", tmp_path)
    monkeypatch.setattr(config, "load_secrets", lambda: {"HA_TOKEN": "t"})
    res = {ha_carbon.CO2_ID: [{"start": 1768464000000, "mean": 234.0}],
           ha_carbon.FOSSIL_ID: [{"start": 1768464000000, "mean": 37.5}]}

    async def fetch(token, windows):
        return [res] + [{} for _ in windows[1:]]
    monkeypatch.setattr(ha_carbon, "_fetch", fetch)
    assert ha_carbon.sync(db, since=date(2026, 1, 14)) == 1
    assert len(list(tmp_path.glob("statistics_*.json"))) == 1

    fresh = store.connect(":memory:")

    async def offline(token, windows):
        raise ConnectionError("offline")
    monkeypatch.setattr(ha_carbon, "_fetch", offline)
    try:
        ha_carbon.sync(fresh)
    except ConnectionError:
        pass
    assert [tuple(r) for r in fresh.execute("select * from grid_carbon_hourly")] == \
        [("2026-01-15T08:00:00Z", 234.0, 37.5)]
