from energy.sources.weather import parse_open_meteo


def test_parse_open_meteo():
    p = {"hourly": {"time": ["2026-01-15T08:00", "2026-01-15T09:00"], "temperature_2m": [30.2, None],
                    "wind_speed_10m": [4.3, 3.5]}}
    assert parse_open_meteo(p, "archive") == [
        {"ts": "2026-01-15T08:00:00Z", "temp_f": 30.2, "wind_mph": 4.3, "source": "archive"}]


def test_tag_provisional_marks_recent_archive_rows_forecast():
    from datetime import datetime, timezone
    from energy.sources.weather import tag_provisional
    now = datetime(2026, 1, 20, 12, tzinfo=timezone.utc)
    rows = [{"ts": "2026-01-10T00:00:00Z", "temp_f": 1, "wind_mph": 1, "source": "archive"},
            {"ts": "2026-01-15T11:00:00Z", "temp_f": 1, "wind_mph": 1, "source": "archive"},
            {"ts": "2026-01-15T12:00:00Z", "temp_f": 1, "wind_mph": 1, "source": "archive"},
            {"ts": "2026-01-19T00:00:00Z", "temp_f": 1, "wind_mph": 1, "source": "archive"}]
    # cutoff is exactly now - 5d = 2026-01-15T12:00; strictly newer is provisional
    assert [r["source"] for r in tag_provisional(rows, now)] == ["archive", "archive", "archive", "forecast"]


def test_later_archive_fetch_replaces_forecast_row(db):
    from energy import store
    store.upsert(db, "weather_hourly", [{"ts": "2026-01-15T00:00:00Z", "temp_f": 1, "wind_mph": 1, "source": "forecast"}])
    store.upsert(db, "weather_hourly", [{"ts": "2026-01-15T00:00:00Z", "temp_f": 2, "wind_mph": 1, "source": "archive"}])
    assert db.execute("select temp_f, source from weather_hourly").fetchall()[0][:] == (2, "archive")


def test_day_weather_notes_forecast_source(db):
    from datetime import date
    from energy import store
    from energy.calc.weather import day_weather
    from energy.daily import compute_day
    hours = store.local_day_hours(date(2026, 1, 15))
    store.upsert(db, "weather_hourly", [{"ts": h, "temp_f": 20.0, "wind_mph": 1.0, "source": "forecast"} for h in hours])
    assert day_weather(db, date(2026, 1, 15))["source"] == "forecast"
    assert any("forecast" in n for n in compute_day(db, date(2026, 1, 15))["notes"])
