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


def test_sync_archives_then_fresh_db_rebuilds_from_archive(db, tmp_path, monkeypatch):
    from energy import config, store
    from energy.sources import emporia
    monkeypatch.setattr(config, "EMPORIA_DIR", tmp_path)
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    first = end - timedelta(hours=3)
    store.upsert(db, "emporia_hourly", [{"channel": "7", "ts": (first - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                         "kwh": 0.0, "channel_name": "heatpump"}])

    class Vue:
        def get_chart_usage(self, ch, start, stop, scale, unit):
            return [1.0, None, 3.0], first
    monkeypatch.setattr(emporia, "_login", lambda: Vue())
    monkeypatch.setattr(emporia, "_channels", lambda vue: [("7", "heatpump", object())])
    assert emporia.sync(db) == 2
    assert len(list(tmp_path.glob("chart_usage_hour_*.json"))) == 1

    fresh = store.connect(":memory:")
    monkeypatch.setattr(emporia, "_login", lambda: (_ for _ in ()).throw(ConnectionError("offline")))
    try:
        emporia.sync(fresh)
    except ConnectionError:
        pass
    q = "select ts, kwh from emporia_hourly where ts >= ? order by ts"
    since = first.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert [tuple(r) for r in fresh.execute(q, (since,))] == [tuple(r) for r in db.execute(q, (since,))]
    assert len(fresh.execute(q, (since,)).fetchall()) == 2


def test_sync_keeps_chunks_fetched_before_a_failure(db, tmp_path, monkeypatch):
    import pytest
    from energy import config
    from energy.sources import emporia
    monkeypatch.setattr(config, "EMPORIA_DIR", tmp_path)
    first = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)

    class Vue:
        def get_chart_usage(self, ch, start, stop, scale, unit):
            if ch == "bad":
                raise ConnectionError("emporia down")
            return [1.0, 2.0], first
    monkeypatch.setattr(emporia, "_login", lambda: Vue())
    monkeypatch.setattr(emporia, "_channels", lambda vue: [("7", "heatpump", "ok"), ("8", "heatpump", "bad")])
    with pytest.raises(ConnectionError):
        emporia.sync(db, since=first.date())
    assert len(list(tmp_path.glob("chart_usage_hour_*.json"))) == 1
    assert db.execute("select count(*) from emporia_hourly where channel='7'").fetchone()[0] == 2
