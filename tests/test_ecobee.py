import json
from pathlib import Path

import pytest

from energy import store
from energy.sources import ecobee
from energy.sources.ecobee import parse_portal_csv, parse_runtime_report

FIXTURES = Path(__file__).parent / "fixtures"
LR, UP = "415533522899", "415522180035"
COLUMNS = ("auxHeat1,compCool1,compHeat1,fan,outdoorTemp,zoneAveTemp,zoneHeatTemp,zoneCoolTemp,"
           "hvacMode,zoneHvacMode,zoneCalendarEvent,zoneClimate")


def payload_with(rows, tid=LR, columns=COLUMNS, sensor_list=None):
    return {"columns": columns,
            "reportList": [{"thermostatIdentifier": tid, "rowList": rows}],
            "sensorList": sensor_list or []}


# Same local times as the rows in tests/fixtures/portal_sample.csv, as the API returns
# them once they are older than the retention window: blank modes, zero runtime.
PRE_RETENTION_PAYLOAD_FOR_SAME_TIMES = payload_with(
    [f"{d},{t},0,0,0,0,30.2,,,,,,," for d, t in
     (("2025-01-01", "00:25:00"), ("2025-01-01", "03:55:00"), ("2025-01-02", "01:40:00"))],
    tid=UP)


def vacation_rows(event, first, last, tid=LR):
    from datetime import date, timedelta
    d0, d1 = date.fromisoformat(first), date.fromisoformat(last)
    rows = []
    d = d0
    while d <= d1:
        for t in ("00:00:00", "12:00:00", "23:55:00"):
            rows.append({"thermostat_id": tid, "ts": ecobee.to_utc(d.isoformat(), t, 0),
                         "heat_setpoint_f": 55, "calendar_event": event, "source": "api"})
        d += timedelta(days=1)
    return rows


def test_parse_runtime_report_drops_pre_retention_rows():
    payload = {"columns": COLUMNS,
               "reportList": [{"thermostatIdentifier": "415533522899", "rowList": [
                   "2024-12-15,00:00:00,0,0,0,0,30.1,,,,,,,",
                   "2026-01-15,00:05:00,0,0,300,300,32.6,65,66,,heat,compressorHeatStage1On,,Sleep"]}],
               "sensorList": []}
    intervals, _ = parse_runtime_report(payload)
    assert len(intervals) == 1 and intervals[0]["comp_heat_s"] == 300


def test_parse_runtime_report_maps_columns_by_name():
    # The 2026-10-31 backup used a 28-column list with HVACmode / zoneHVACmode spellings.
    cols = ("auxHeat1,auxHeat2,auxHeat3,compCool1,compCool2,compHeat1,compHeat2,dehumidifier,dmOffset,"
            "economizer,fan,humidifier,HVACmode,outdoorHumidity,outdoorTemp,sky,ventilator,wind,"
            "zoneAveTemp,zoneCalendarEvent,zoneClimate,zoneCoolTemp,zoneHeatTemp,zoneHumidity,"
            "zoneHumidityHigh,zoneHumidityLow,zoneHVACmode,zoneOccupancy")
    row = ("2026-01-15,00:05:00,10,0,0,20,0,300,0,0,-0.4,0,280,0,heat,82,32.6,5,0,0,"
           "65.1,176645687997,Home,76,66,46,0,0,compressorHeatStage1On,0")
    [r], _ = parse_runtime_report(payload_with([row], columns=cols))
    assert r == {"thermostat_id": LR, "ts": "2026-01-15T08:05:00Z", "comp_heat_s": 300,
                 "comp_cool_s": 20, "aux_heat_s": 10, "fan_s": 280, "outdoor_temp_f": 32.6,
                 "indoor_temp_f": 65.1, "heat_setpoint_f": 66, "cool_setpoint_f": 76,
                 "hvac_mode": "heat", "zone_hvac_mode": "compressorHeatStage1On",
                 "calendar_event": "176645687997", "climate": "Home", "source": "api"}


def test_parse_runtime_report_sensors():
    sensors = [{"thermostatIdentifier": UP, "sensors": [
        {"sensorId": "rs:100:1", "sensorName": "Kids' Room", "sensorType": "temperature"},
        {"sensorId": "rs:100:2", "sensorName": "Kids' Room", "sensorType": "occupancy"},
        {"sensorId": "rs2:101:1", "sensorName": "crawlspace ", "sensorType": "temperature"}],
        "columns": ["date", "time", "rs:100:1", "rs:100:2", "rs2:101:1"],
        "data": ["2026-01-15,00:05:00,65.4,1,", "2026-01-15,00:10:00,65.5,0,69.2"]}]
    _, rows = parse_runtime_report(payload_with([], sensor_list=sensors))
    got = {(r["thermostat_id"], r["ts"], r["sensor_name"], r["kind"]): r["value"] for r in rows}
    assert got == {
        (UP, "2026-01-15T08:05:00Z", "Kids' Room", "temperature"): 65.4,
        (UP, "2026-01-15T08:05:00Z", "Kids' Room", "occupancy"): 1,
        (UP, "2026-01-15T08:10:00Z", "Kids' Room", "temperature"): 65.5,
        (UP, "2026-01-15T08:10:00Z", "Kids' Room", "occupancy"): 0,
        (UP, "2026-01-15T08:10:00Z", "crawlspace", "temperature"): 69.2,
    }


def test_to_utc_local_basis():
    assert ecobee.ECOBEE_TS_IS_UTC is False
    assert ecobee.to_utc("2026-01-15", "00:05:00", 0) == "2026-01-15T08:05:00Z"
    assert ecobee.to_utc("2025-07-01", "12:00:00", 0) == "2025-07-01T19:00:00Z"
    assert ecobee.to_utc("2025-11-02", "01:30:00", 0) == "2025-11-02T08:30:00Z"
    assert ecobee.to_utc("2025-11-02", "01:30:00", 1) == "2025-11-02T09:30:00Z"


def test_api_rows_do_not_clobber_portal_rows(db):            # Review Focus 3
    store.upsert(db, "ecobee_interval", parse_portal_csv(FIXTURES / "portal_sample.csv")[0])
    before = db.execute("select count(*) from ecobee_interval where source='portal_csv'").fetchone()[0]
    store.upsert(db, "ecobee_interval", parse_runtime_report(PRE_RETENTION_PAYLOAD_FOR_SAME_TIMES)[0])
    assert db.execute("select count(*) from ecobee_interval where source='portal_csv'").fetchone()[0] == before
    assert before == 3


def test_portal_import_does_not_replace_api_rows(db):
    row = "2025-01-01,00:25:00,0,0,0,0,30.2,64,65,72,heat,heatOff,,Sleep"
    store.upsert(db, "ecobee_interval", parse_runtime_report(payload_with([row], tid=UP))[0])
    ecobee.import_portal_csv(db, FIXTURES / "portal_sample.csv")
    got = dict(tuple(r) for r in db.execute("select ts, source from ecobee_interval"))
    assert got["2025-01-01T08:25:00Z"] == "api"
    assert sorted(got.values()) == ["api", "portal_csv", "portal_csv"]
    sensor_ts = {r[0] for r in db.execute("select ts from ecobee_sensor where sensor_name = 'Bedroom'")}
    assert "2025-01-01T08:25:00Z" not in sensor_ts and len(sensor_ts) == 2  # API time keeps no portal sensors


@pytest.mark.skipif(ecobee.ECOBEE_TS_IS_UTC, reason="UTC timestamps have no repeated hour")
def test_fall_back_hour_keeps_both_copies():                  # Review Focus 2
    rows = [f"2025-11-02,01:{m:02d}:00,0,0,0,0,40,65,66,,heat,heatOff,,Sleep" for m in range(0, 60, 5)] * 2
    intervals, _ = parse_runtime_report(payload_with(rows))
    assert len({r["ts"] for r in intervals}) == 24


def test_portal_csv_upstairs_heat_goes_to_aux():
    rows, sensors = parse_portal_csv(FIXTURES / "portal_sample.csv")   # Upstairs sample, a row with Heat Stage 1 = 195
    assert any(r["aux_heat_s"] == 195 and r["comp_heat_s"] == 0 for r in rows)
    r = rows[1]
    assert r["thermostat_id"] == UP and r["ts"] == "2025-01-01T11:55:00Z"
    assert (r["fan_s"], r["indoor_temp_f"], r["outdoor_temp_f"]) == (45, 64.6, 29.2)
    assert (r["heat_setpoint_f"], r["cool_setpoint_f"]) == (65, 72)
    assert (r["hvac_mode"], r["zone_hvac_mode"], r["climate"]) == ("heat", "heatStage1On", "Sleep")
    assert r["calendar_event"] is None and r["source"] == "portal_csv"
    got = {(x["sensor_name"], x["kind"]): x["value"] for x in sensors if x["ts"] == r["ts"]}
    assert got == {("Thermostat Temperature", "temperature"): 64.3, ("Thermostat Humidity", "humidity"): 38,
                   ("Kids' Room", "temperature"): 65.1, ("Kids' Room", "occupancy"): 0,
                   ("Office Guest Room", "temperature"): 61.7, ("Office Guest Room", "occupancy"): 0,
                   ("Bedroom", "temperature"): 64.7, ("Bedroom", "occupancy"): 1}


def test_portal_csv_living_room_mapping(tmp_path):
    p = tmp_path / "report-lr.csv"
    p.write_text(
        "﻿#,Thermostat,identifier,415533522899\n#,Thermostat,name,Living Room\n"
        "#,Start,date,2026-01-01\n#,End,date,2026-01-30\n\n"
        "Date,Time,System Setting,System Mode,Calendar Event,Program Mode,Cool Set Temp (F),"
        "Heat Set Temp (F),Current Temp (F),Current Humidity (%RH),Outdoor Temp (F),Wind Speed (km/h),"
        "Cool Stage 1 (sec),Heat Stage 1 (sec),Aux Heat 1 (sec),Fan (sec),DM Offset\n"
        "2026-01-01,00:00:00,heat,compressorHeatStage1On,,Sleep,74,66,64.9,31,30.5,0,0,300,15,300,,\n"
        "2026-01-01,00:05:00,,,,,,,,,30.5,0,,,,,,\n")
    [r], _ = parse_portal_csv(p)
    assert r["thermostat_id"] == LR and r["comp_heat_s"] == 300 and r["aux_heat_s"] == 15


def test_derive_vacations(db):
    store.upsert(db, "ecobee_interval", vacation_rows("176645687997", "2025-12-22", "2025-12-30"))
    ecobee.derive_vacations(db)
    assert [tuple(r) for r in db.execute("select start_date, end_date from not_home where source='ecobee_vacation'").fetchall()] == [("2025-12-22", "2025-12-30")]


def test_derive_vacations_name_forms(db):
    store.upsert(db, "ecobee_interval", vacation_rows("14B37B900E1F", "2025-06-01", "2025-06-03"))
    store.upsert(db, "ecobee_interval", vacation_rows("176645687997(SmartRecovery)", "2025-12-22", "2025-12-22"))
    store.upsert(db, "ecobee_interval", vacation_rows("176645687997", "2025-12-23", "2025-12-24"))
    store.upsert(db, "ecobee_interval", vacation_rows("Home", "2025-08-01", "2025-08-02", tid=UP))
    n = ecobee.derive_vacations(db)
    got = [tuple(r) for r in db.execute("select start_date, end_date from not_home order by 1")]
    assert got == [("2025-06-01", "2025-06-03"), ("2025-12-22", "2025-12-24")] and n == 2


def test_access_token_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ecobee.config, "SECRETS_DIR", tmp_path)
    with pytest.raises(ecobee.EcobeeLoginRequired, match="energy ecobee-login"):
        ecobee.access_token()


def test_access_token_saves_rotated_refresh_token(tmp_path, monkeypatch):
    monkeypatch.setattr(ecobee.config, "SECRETS_DIR", tmp_path)
    (tmp_path / "ecobee_tokens.json").write_text(json.dumps({"access_token": "a0", "refresh_token": "r0"}))
    sent = {}

    class Resp:
        status_code = 200
        def json(self):
            return {"access_token": "a1", "refresh_token": "r1"}

    def fake(method, url, **kw):
        sent.update(kw["data"])
        return Resp()
    monkeypatch.setattr(ecobee.http, "request", fake)
    assert ecobee.access_token() == "a1"
    assert sent["refresh_token"] == "r0" and sent["grant_type"] == "refresh_token"
    saved = json.loads((tmp_path / "ecobee_tokens.json").read_text())
    assert saved == {"access_token": "a1", "refresh_token": "r1"}
    assert (tmp_path / "ecobee_tokens.json").stat().st_mode & 0o777 == 0o600


def test_access_token_rejected_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(ecobee.config, "SECRETS_DIR", tmp_path)
    (tmp_path / "ecobee_tokens.json").write_text(json.dumps({"access_token": "a0", "refresh_token": "r0"}))

    class Resp:
        status_code = 400
        def json(self):
            return {"error": "invalid_grant"}
    monkeypatch.setattr(ecobee.http, "request", lambda *a, **k: Resp())
    with pytest.raises(ecobee.EcobeeLoginRequired, match="energy ecobee-login"):
        ecobee.access_token()


def test_sync_never_overwrites_archive_files(db, tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    api_dir, portal_dir = tmp_path / "api", tmp_path / "portal"
    api_dir.mkdir()
    portal_dir.mkdir()
    monkeypatch.setattr(ecobee.config, "ECOBEE_API_DIR", api_dir)
    monkeypatch.setattr(ecobee.config, "ECOBEE_PORTAL_DIR", portal_dir)
    monkeypatch.setattr(ecobee, "access_token", lambda: "tok")
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=40)
    first_end = since + timedelta(days=30)
    # The backup file sync's first window would previously have overwritten.
    original = api_dir / f"runtimeReport_{since}_{first_end}.json"
    good = f"{since},12:00:00,0,0,300,300,32.6,65,66,,heat,compressorHeatStage1On,,Sleep"
    original.write_text(json.dumps(payload_with([good])))
    original_bytes = original.read_bytes()
    windows = []

    def fake_fetch(token, start, end):
        windows.append((start, end))
        return payload_with([f"{start},12:00:00,0,0,0,0,30.1,,,,,,,"])  # aged-out filler
    monkeypatch.setattr(ecobee, "_fetch", fake_fetch)

    ecobee.sync(db, since=since)

    assert windows == [(since, first_end), (first_end + timedelta(days=1), today)]
    assert original.read_bytes() == original_bytes
    new = sorted(p.name for p in api_dir.glob("*.json") if p != original)
    assert len(new) == 2 and all("_fetched" in n for n in new)
    assert new[0].startswith(f"runtimeReport_{since}_{first_end}_fetched")
    # The backup's good row survived; filler rows were dropped.
    assert db.execute("select count(*) from ecobee_interval").fetchone()[0] == 1
    # A second sync imports nothing again from the files and still writes fresh unique names.
    before = len(list(api_dir.glob("*.json")))
    ecobee.sync(db, since=since)
    assert original.read_bytes() == original_bytes
    assert len(list(api_dir.glob("*.json"))) >= before
    assert db.execute("select count(*) from sync_log where source='ecobee_file'").fetchone()[0] >= 3


def test_write_archive_does_not_overwrite_same_second(tmp_path):
    from energy import archive
    a = archive.write_json(tmp_path, "runtimeReport_2026-01-01_2026-01-31", {"n": 1})
    b = archive.write_json(tmp_path, "runtimeReport_2026-01-01_2026-01-31", {"n": 2})
    assert a != b and json.loads(a.read_text()) == {"n": 1} and json.loads(b.read_text()) == {"n": 2}


def test_access_token_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ecobee.config, "SECRETS_DIR", tmp_path)
    ecobee._token_path().write_text("{not json")
    with pytest.raises(ecobee.EcobeeLoginRequired):
        ecobee.access_token()


def test_sync_derives_vacations_even_when_login_required(db, tmp_path, monkeypatch):
    api_dir, portal_dir = tmp_path / "api", tmp_path / "portal"
    api_dir.mkdir()
    portal_dir.mkdir()
    monkeypatch.setattr(ecobee.config, "ECOBEE_API_DIR", api_dir)
    monkeypatch.setattr(ecobee.config, "ECOBEE_PORTAL_DIR", portal_dir)
    store.upsert(db, "ecobee_interval", vacation_rows("176645687997", "2025-12-22", "2025-12-30"))

    def boom():
        raise ecobee.EcobeeLoginRequired("run `energy ecobee-login`")
    monkeypatch.setattr(ecobee, "access_token", boom)
    with pytest.raises(ecobee.EcobeeLoginRequired):
        ecobee.sync(db)
    assert db.execute("select count(*) from not_home where source='ecobee_vacation'").fetchone()[0] == 1


def test_interrupted_archive_write_leaves_no_json(tmp_path, monkeypatch):
    from pathlib import Path
    from energy import archive
    real = Path.write_bytes

    def half_then_fail(self, data):
        real(self, data[: len(data) // 2])
        raise KeyboardInterrupt
    monkeypatch.setattr(Path, "write_bytes", half_then_fail)
    with pytest.raises(KeyboardInterrupt):
        archive.write_json(tmp_path, "runtimeReport_2026-01-01_2026-01-31", {"rows": list(range(100))})
    assert list(tmp_path.glob("*.json")) == []


def test_api_start_resumes_from_older_thermostat(db):
    from datetime import date
    from energy import config
    today = date(2026, 11, 1)
    assert ecobee._api_start(db, None, today) == date(2025, 3, 11)  # nothing stored: default lookback
    store.upsert(db, "ecobee_interval", [
        {"thermostat_id": config.LR_ECOBEE_ID, "ts": "2026-10-31T12:00:00Z", "source": "api"},
        {"thermostat_id": config.UP_ECOBEE_ID, "ts": "2026-10-20T12:00:00Z", "source": "api"},
        {"thermostat_id": config.UP_ECOBEE_ID, "ts": "2026-10-30T12:00:00Z", "source": "portal_csv"}])
    assert ecobee._api_start(db, None, today) == date(2026, 10, 18)
    assert ecobee._api_start(db, date(2026, 1, 1), today) == date(2026, 1, 1)
