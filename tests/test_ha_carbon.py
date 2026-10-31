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
