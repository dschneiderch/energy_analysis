import pytest

from energy import config, store
from energy.calc import house_profile, profile as prof

LR, UP = config.LR_ECOBEE_ID, config.UP_ECOBEE_ID
TS, T = "2026-01-01T12:00:00Z", 1_767_268_800


def _interval(tid, **kw):
    return {"thermostat_id": tid, "ts": TS, "comp_heat_s": 0, "comp_cool_s": 0, "aux_heat_s": 0,
            "indoor_temp_f": 68.0, "outdoor_temp_f": 30.0, "hvac_mode": "heat", "calendar_event": None,
            "climate": "Sleep", "source": "api", **kw}


def test_each_thermostat_mapped_by_its_own_heat_type_and_rooms_replace_indoor(db):
    store.upsert(db, "ecobee_interval", [_interval(LR, comp_heat_s=300), _interval(UP, aux_heat_s=120)])
    store.upsert(db, "ecobee_sensor", [
        {"thermostat_id": UP, "ts": TS, "sensor_name": n, "kind": "temperature", "value": v}
        for n, v in (("Bedroom", 66.0), ("Kids' Room", 67.0), ("Office Guest Room", 71.0),
                     ("Thermostat Temperature", 80.0))])
    rows = house_profile.load_rows(db, T, T, {UP: house_profile.UPSTAIRS_ROOMS})[T]
    # The beestat port would map the Living Room by Upstairs' type and lose its compressor heat.
    assert prof.load_rows(db, T, T, "electric")[T][LR]["heat_1"] == 0
    assert rows[LR]["heat_1"] == 300 and rows[UP]["heat_1"] == 120
    assert rows[UP]["indoor"] == pytest.approx(68.0) and rows[UP]["climate"] is None
    assert rows[LR]["indoor"] == 68.0 and rows[LR]["climate"] == "Sleep"


def test_missing_room_reading_makes_indoor_unknown(db):
    store.upsert(db, "ecobee_interval", [_interval(UP)])
    store.upsert(db, "ecobee_sensor", [{"thermostat_id": UP, "ts": TS, "sensor_name": "Bedroom",
                                        "kind": "temperature", "value": 66.0}])
    assert house_profile.load_rows(db, T, T, {UP: house_profile.UPSTAIRS_ROOMS})[T][UP]["indoor"] is None


def test_heating_season_keeps_october_to_may_parts():
    from datetime import date
    assert house_profile.heating_season(date(2025, 4, 16), date(2025, 11, 23)) == [
        (date(2025, 4, 16), date(2025, 5, 31)), (date(2025, 10, 1), date(2025, 11, 23))]
    assert house_profile.heating_season(date(2025, 12, 2), date(2026, 11, 7)) == [
        (date(2025, 12, 2), date(2026, 5, 31)), (date(2026, 10, 1), date(2026, 11, 7))]
    assert house_profile.heating_season(date(2026, 6, 1), date(2026, 9, 30)) == []


def test_emporia_gate_marks_other_thermostat_on_in_hours_with_heat_pump_power(db):
    rows = {T + k * 300: {UP: {"ts": T + k * 300, "indoor": 68.0, "outdoor": 30, "heat_1": 0, "auxiliary_heat_1": 0,
                                "cool_1": 0, "event": None, "climate": None}} for k in range(24)}
    store.upsert(db, "emporia_hourly", [  # 12:00 hour off (standby), 13:00 hour on
        {"channel": ch, "ts": ts, "kwh": kwh, "channel_name": "hp"}
        for ch, ts, kwh in (("7", "2026-01-01T12:00:00Z", 0.02), ("8", "2026-01-01T12:00:00Z", 0.015),
                            ("7", "2026-01-01T13:00:00Z", 0.5), ("8", "2026-01-01T13:00:00Z", 0.4))])
    house_profile.gate_with_emporia(db, rows, UP, T, T + 23 * 300)
    assert [rows[T + k * 300][LR]["heat_1"] for k in (0, 11, 12, 23)] == [0, 0, 300, 300]
    assert rows[T][LR]["outdoor"] == 30 and rows[T][LR]["indoor"] is not None


def test_room_average_resist_minimum_is_15_minutes_beestat_keeps_30():
    from test_profile import T0, _off_rows
    rows = _off_rows(8, outdoor=lambda k: 30.0 if k < 5 else 31.0)   # off for 20 min before the outdoor change
    assert prof.samples(rows, LR, T0, T0 + 7 * 300)["resist"] == {}
    minimum = {**prof.MIN_DURATION, "resist": house_profile.ROOM_AVERAGE_MIN_RESIST_MINUTES * 60}
    s = prof.samples(rows, LR, T0, T0 + 7 * 300, minimum)
    assert s["resist"][30] == [pytest.approx(-1.2)]
