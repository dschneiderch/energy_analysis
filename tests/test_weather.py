from energy.sources.weather import parse_open_meteo


def test_parse_open_meteo():
    p = {"hourly": {"time": ["2026-01-15T08:00", "2026-01-15T09:00"], "temperature_2m": [30.2, None],
                    "wind_speed_10m": [4.3, 3.5]}}
    assert parse_open_meteo(p, "archive") == [
        {"ts": "2026-01-15T08:00:00Z", "temp_f": 30.2, "wind_mph": 4.3, "source": "archive"}]
