from datetime import datetime, timedelta, timezone

from energy.sources.emporia import rows_from_chart

UTC = timezone.utc


def test_rows_from_chart_drops_none_and_trailing():
    first = datetime(2026, 1, 15, 8, tzinfo=UTC)
    end = first + timedelta(hours=2)
    rows = rows_from_chart("7", "heatpump", [1.0, None, 3.0], first, end)
    assert [(r["ts"], r["kwh"]) for r in rows] == [("2026-01-15T08:00:00Z", 1.0)]


def test_start_for_channel_resumes_from_sync_log_when_no_rows(db):
    from datetime import date
    from energy import store
    from energy.sources.emporia import start_for_channel
    store.log_sync(db, "emporia:10", "2026-01-01T00:00:00Z", "2026-01-20T00:00:00Z", 0, "ok")
    assert start_for_channel(db, "10", None).date() == date(2026, 1, 18)
    assert start_for_channel(db, "11", None).date() == date(2023, 4, 15)
