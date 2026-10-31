from datetime import datetime, timedelta, timezone

from energy.sources.emporia import rows_from_chart

UTC = timezone.utc


def test_rows_from_chart_drops_none_and_trailing():
    first = datetime(2026, 1, 15, 8, tzinfo=UTC)
    end = first + timedelta(hours=2)
    rows = rows_from_chart("7", "heatpump", [1.0, None, 3.0], first, end)
    assert [(r["ts"], r["kwh"]) for r in rows] == [("2026-01-15T08:00:00Z", 1.0)]
