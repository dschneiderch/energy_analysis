from energy.sources.beestat import parse_summaries

ROW = {"runtime_thermostat_summary_id": 47512207, "user_id": 28979, "thermostat_id": 47722,
       "date": "2022-11-25", "count": 288, "sum_compressor_cool_1": 0, "sum_compressor_cool_2": 0,
       "sum_compressor_heat_1": 0, "sum_compressor_heat_2": 0, "sum_auxiliary_heat_1": 7140,
       "sum_auxiliary_heat_2": 0, "sum_fan": 7770, "sum_humidifier": 0, "sum_dehumidifier": 0,
       "sum_ventilator": 0, "sum_economizer": 0, "sum_heating_degree_days": 25.4,
       "sum_cooling_degree_days": 0, "avg_outdoor_temperature": 39.6, "avg_outdoor_humidity": 66,
       "min_outdoor_temperature": 33.1, "max_outdoor_temperature": 47.4,
       "avg_indoor_temperature": 63.1, "avg_indoor_humidity": 44, "deleted": False}


def test_parse_summaries_maps_fields():
    [r] = parse_summaries([ROW])
    assert r["thermostat_id"] == 47722 and r["date"] == "2022-11-25"
    assert r["aux_heat_s"] == 7140 and r["fan_s"] == 7770 and r["avg_outdoor_f"] == 39.6
    assert r["comp_heat_s"] == 0 and r["interval_count"] == 288 and r["source"] == "beestat"
    assert r["min_outdoor_f"] == 33.1 and r["max_outdoor_f"] == 47.4 and r["avg_indoor_f"] == 63.1


def test_parse_summaries_skips_deleted():
    assert parse_summaries([{**ROW, "deleted": True}]) == []


def test_sync_refreshes_reads_recent_week_archives_raw_and_rebuilds(db, tmp_path, monkeypatch):
    import json
    import pytest
    from energy import config, store
    from energy.sources import beestat
    monkeypatch.setattr(config, "BEESTAT_DIR", tmp_path)
    monkeypatch.setattr(config, "load_secrets", lambda: {"BEESTAT_API_KEY": "k"})
    lr, up = {**ROW, "thermostat_id": 47724, "date": "2026-10-20"}, {**ROW, "thermostat_id": 47723, "date": "2026-10-25"}
    (tmp_path / "runtime_thermostat_summary_2026-10-31.json").write_text(json.dumps([lr, up]))  # rows-only format
    revised = {**lr, "date": "2026-10-20", "sum_fan": 9999}
    body = {"success": True, "data": {"1": revised}, "future_field": 1}
    calls = []

    class Resp:
        status_code = 200
        def __init__(self, b):
            self.b = b
        def json(self):
            return self.b

    def request(method, url, params, headers):
        calls.append((params["resource"], params["method"], json.loads(params.get("arguments", "null"))))
        return Resp({"success": True, "data": None} if params["resource"] == "runtime" else body)
    monkeypatch.setattr(beestat.http, "request", request)
    beestat.sync(db)
    # Only live thermostats are refreshed; the read starts a week before the older one's newest day.
    assert calls == [("runtime", "sync", {"thermostat_id": 47724}), ("runtime", "sync", {"thermostat_id": 47723}),
                     ("runtime_thermostat_summary", "read_id",
                      {"attributes": {"date": {"value": "2026-10-13", "operator": ">="}}})]
    [new_file] = tmp_path.glob("*_fetched*.json")
    assert json.loads(new_file.read_text()) == body

    fresh = store.connect(":memory:")
    monkeypatch.setattr(beestat.http, "request", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("offline")))
    with pytest.raises(ConnectionError):
        beestat.sync(fresh)
    q = "select thermostat_id, date, fan_s from ecobee_daily order by date"
    assert [tuple(r) for r in fresh.execute(q)] == [tuple(r) for r in db.execute(q)] == \
        [("47724", "2026-10-20", 9999), ("47723", "2026-10-25", 7770)]


def test_read_start_reads_everything_until_both_thermostats_have_data(db):
    from datetime import date
    from energy import store
    from energy.sources import beestat
    assert beestat._read_start(db, None) is None
    store.upsert(db, "ecobee_daily", [{"thermostat_id": 47724, "date": "2026-10-30"}])
    assert beestat._read_start(db, None) is None
    assert beestat._read_start(db, date(2025, 1, 1)) == date(2025, 1, 1)
