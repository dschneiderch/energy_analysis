from datetime import date, datetime, timedelta, timezone

import pytest

from energy import config, store
from energy.calc.carbon import day_carbon
from energy.calc.cost import electric_cost, gas_day
from energy.calc.energy import day_energy
from energy.calc.rates import RateSet
from energy.calc.runtime import day_runtime
from energy.calc.weather import day_weather, degree_days

LR, UP = config.LR_ECOBEE_ID, config.UP_ECOBEE_ID
FMT = "%Y-%m-%dT%H:%M:%SZ"


# ---------- helpers ----------

def intervals(day):
    start, end = (datetime.strptime(s, FMT).replace(tzinfo=timezone.utc) for s in store.local_day_bounds(day))
    out = []
    while start < end:
        out.append(start.strftime(FMT))
        start += timedelta(minutes=5)
    return out


def hour_of(ts):
    return ts[:13] + ":00:00Z"


def fill_day(db, day, values=None, tids=(LR, UP)):
    """Insert a full local day of 5-min rows for each thermostat. values: {(tid, ts): {col: v}}."""
    values = values or {}
    rows = []
    for tid in tids:
        for ts in intervals(day):
            row = {"thermostat_id": tid, "ts": ts, "comp_heat_s": 0.0, "comp_cool_s": 0.0,
                   "aux_heat_s": 0.0, "fan_s": 0.0, "outdoor_temp_f": None}
            row.update(values.get((tid, ts), {}))
            rows.append(row)
    store.upsert(db, "ecobee_interval", rows)


def fill_hp(db, day, kwh_per_channel=1.0, overrides=None):
    """Channels 7 and 8 for every local hour; overrides: {hour_ts: kwh_per_channel}."""
    overrides = overrides or {}
    rows = []
    for h in store.local_day_hours(day):
        for ch in config.HP_CHANNELS:
            rows.append({"channel": ch, "ts": h, "kwh": overrides.get(h, kwh_per_channel), "channel_name": "heatpump"})
    store.upsert(db, "emporia_hourly", rows)


def fill_carbon(db, hours, co2=200.0, fossil=40.0, overrides=None):
    overrides = overrides or {}
    rows = [{"ts": h, "co2_g_per_kwh": overrides.get(h, (co2, fossil))[0],
             "fossil_pct": overrides.get(h, (co2, fossil))[1]} for h in hours]
    store.upsert(db, "grid_carbon_hourly", rows)


# ---------- cost ----------

def test_tier_split():
    rs = RateSet(date(2025, 10, 1), [(0, 600, 0.10), (600, None, 0.20)], tax_adj=0.0, basic=20.0, is_fallback=False)
    out = electric_cost(rs, prior_house_kwh=590, rest_of_house_kwh=0, kwh_by_category={"heating": 20})
    assert out["cost_usd"] == pytest.approx(10 * 0.10 + 10 * 0.20)


def test_cost_by_category_and_tax():
    rs = RateSet(date(2025, 10, 1), [(0, 600, 0.10), (600, None, 0.20)], tax_adj=0.01, basic=20.0, is_fallback=False)
    out = electric_cost(rs, prior_house_kwh=0, rest_of_house_kwh=0, kwh_by_category={"heating": 30, "other": 10})
    assert out["cost_usd"] == pytest.approx(40 * 0.11)
    assert out["cost_by_category"]["heating"] == pytest.approx(30 * 0.11)
    assert out["cost_by_category"]["other"] == pytest.approx(10 * 0.11)


def test_gas_day(db):
    store.upsert(db, "gas_daily", [{"date": "2022-12-15", "therms": 2.0, "ccf": 2.0, "in_town": 1}])
    store.upsert(db, "rates", [
        {"fuel": "gas", "schedule": "101", "effective_date": "2022-11-01", "component": "energy",
         "tier_min": 0, "tier_max": None, "rate_per_unit": 1.03788, "unit": "$/therm"},
        {"fuel": "gas", "schedule": "101", "effective_date": "2022-11-01", "component": "basic",
         "tier_min": 0, "tier_max": None, "rate_per_unit": 7.0, "unit": "$/month"}])
    g = gas_day(db, date(2022, 12, 15))
    assert g["gas_therms"] == 2.0
    assert g["gas_kwh_equiv"] == pytest.approx(2 * 29.3071)
    assert g["furnace_heat_kwh"] == pytest.approx(2 * 29.3071 * 0.95)
    assert g["gas_cost_usd"] == pytest.approx(2 * 1.03788)
    assert g["gas_gco2e"] == pytest.approx(2 * 5306)
    assert gas_day(db, date(2022, 12, 16)) is None


# ---------- carbon ----------

D = date(2025, 3, 10)


def test_carbon_weighting(db):
    hours = store.local_day_hours(D)
    H1, H2 = hours[3], hours[10]
    fill_carbon(db, hours, overrides={H1: (100.0, 30.0), H2: (300.0, 50.0)})
    c = day_carbon(db, D, {H1: 1.0, H2: 3.0}, {H1: {"heating": 1.0}, H2: {"heating": 3.0}})
    assert c["gco2e"] == pytest.approx(1 * 100 + 3 * 300)
    assert c["low_carbon_pct"] == pytest.approx((1 * 70 + 3 * 50) / 4)
    assert c["gco2e_by_category"]["heating"] == pytest.approx(1000)
    assert c["filled_hours"] == []


def test_carbon_fills_missing_hours_with_mean(db):
    hours = store.local_day_hours(D)
    fill_carbon(db, hours[:20], co2=200.0, fossil=40.0)
    c = day_carbon(db, D, {hours[22]: 2.0}, {hours[22]: {"cooling": 2.0}})
    assert c["gco2e"] == pytest.approx(400.0)
    assert c["filled_hours"] == hours[20:]


def test_carbon_unavailable_with_few_hours(db):
    hours = store.local_day_hours(D)
    H1 = hours[0]
    fill_carbon(db, hours[:11])
    c = day_carbon(db, D, {H1: 1.0}, {H1: {"heating": 1.0}})
    assert c["gco2e"] is None and c["reason"]


def test_carbon_unavailable_before_2024_02(db):
    c = day_carbon(db, date(2023, 12, 1), {}, {})
    assert c["gco2e"] is None and c["reason"] == "no grid carbon data before 2024-02"


# ---------- runtime ----------

def test_runtime_union_and_preinstall_aux(db):
    day = date(2025, 1, 15)
    ts = intervals(day)
    fill_day(db, day, {
        (LR, ts[10]): {"comp_heat_s": 300.0},
        (UP, ts[10]): {"aux_heat_s": 300.0},      # same interval: counts once
        (LR, ts[20]): {"aux_heat_s": 300.0},      # pre-strip LR aux = heat pump heat
        (UP, ts[30]): {"aux_heat_s": 120.0},
    })
    rt = day_runtime(db, day)
    expected = (300 + 300 + 120) / 3600
    assert rt.basis == "5min" and rt.heat_h == pytest.approx(expected) and rt.aux_h == 0
    assert rt.zones["LR"]["heat"] == pytest.approx(600 / 3600)
    assert rt.zones["UP"]["heat"] == pytest.approx(420 / 3600)
    assert rt.hourly[hour_of(ts[10])]["heat_s"] == pytest.approx(300)


def test_runtime_post_strip_aux_is_aux(db):
    day = date(2025, 12, 10)
    ts = intervals(day)
    fill_day(db, day, {(LR, ts[5]): {"aux_heat_s": 300.0, "comp_heat_s": 300.0}})
    rt = day_runtime(db, day)
    assert rt.heat_h == pytest.approx(300 / 3600) and rt.aux_h == pytest.approx(300 / 3600)


def test_runtime_daily_fallback_and_furnace(db):
    store.upsert(db, "ecobee_daily", [
        {"thermostat_id": str(config.BEESTAT_OLD), "date": "2022-12-15",
         "comp_heat_s": 0.0, "comp_cool_s": 0.0, "aux_heat_s": 7200.0, "fan_s": 0.0}])
    rt = day_runtime(db, date(2022, 12, 15))
    assert rt.basis == "daily_lower_bound"
    assert rt.furnace_h == pytest.approx(2.0) and rt.heat_h == 0 and rt.hourly == {}
    assert "OLD" in rt.zones


def test_runtime_daily_uses_lr_after_start(db):
    store.upsert(db, "ecobee_daily", [
        {"thermostat_id": str(config.BEESTAT_LR), "date": "2024-01-10",
         "comp_heat_s": 3600.0, "comp_cool_s": 0.0, "aux_heat_s": 1800.0, "fan_s": 0.0},
        {"thermostat_id": str(config.BEESTAT_UP), "date": "2024-01-10",
         "comp_heat_s": 0.0, "comp_cool_s": 0.0, "aux_heat_s": 9000.0, "fan_s": 0.0}])
    rt = day_runtime(db, date(2024, 1, 10))
    assert rt.basis == "daily_lower_bound" and rt.heat_h == pytest.approx(2.5) and rt.furnace_h == 0


def test_runtime_none_without_data(db):
    rt = day_runtime(db, date(2020, 6, 1))
    assert rt.basis is None and rt.heat_h is None


# ---------- energy ----------

def test_dst_day_has_25_hours(db):
    day = date(2025, 11, 2)
    assert len(store.local_day_hours(day)) == 25
    fill_hp(db, day, 1.0)
    rt = day_runtime(db, day)
    assert day_energy(db, day, rt).total_kwh == pytest.approx(25 * 2.0)


def test_energy_none_when_hour_missing(db):
    day = date(2025, 12, 10)
    fill_hp(db, day, 1.0)
    db.execute("DELETE FROM emporia_hourly WHERE channel='8' AND ts=?", (store.local_day_hours(day)[5],))
    fill_day(db, day)
    e = day_energy(db, day, day_runtime(db, day))
    assert e.total_kwh is None and e.heating_kwh is None and e.reason


def test_strip_kwh(db):
    day = date(2025, 12, 10)
    ts = intervals(day)
    fill_day(db, day, {(LR, ts[i]): {"aux_heat_s": 300.0} for i in range(24, 30)})  # 30 min in one hour
    fill_hp(db, day, 1.0)
    e = day_energy(db, day, day_runtime(db, day))
    hp_heating = 2.0
    assert e.strip_kwh == pytest.approx(2.5) and e.heating_kwh == pytest.approx(hp_heating + 2.5)
    assert e.total_kwh == pytest.approx(len(store.local_day_hours(day)) * 2.0 + 2.5)
    assert e.hourly_kwh[hour_of(ts[24])] == pytest.approx(4.5)


def test_cooling_hour_goes_to_cooling(db):
    day = date(2025, 7, 15)
    ts = intervals(day)
    fill_day(db, day, {(UP, ts[50]): {"comp_cool_s": 300.0}})
    cool_hour = hour_of(ts[50])
    fill_hp(db, day, 0.1, overrides={cool_hour: 1.5})
    e = day_energy(db, day, day_runtime(db, day))
    cooling_hour_kwh = 3.0
    assert e.cooling_kwh == pytest.approx(cooling_hour_kwh) and e.heating_kwh == 0
    assert e.other_kwh == pytest.approx(23 * 0.2)


def test_mixed_hour_split_by_runtime(db):
    day = date(2025, 7, 15)
    ts = intervals(day)
    fill_day(db, day, {(LR, ts[0]): {"comp_heat_s": 100.0}, (LR, ts[1]): {"comp_cool_s": 300.0}})
    fill_hp(db, day, 0.0, overrides={hour_of(ts[0]): 2.0})
    e = day_energy(db, day, day_runtime(db, day))
    assert e.heating_kwh == pytest.approx(1.0) and e.cooling_kwh == pytest.approx(3.0)


def test_house_and_month_to_date(db):
    day = date(2025, 12, 3)
    rows = []
    for d in (date(2025, 12, 1), date(2025, 12, 2), day):
        rows += [{"channel": config.MAINS_CHANNEL, "ts": h, "kwh": 1.0, "channel_name": "mains"}
                 for h in store.local_day_hours(d)]
    store.upsert(db, "emporia_hourly", rows)
    fill_hp(db, day, 0.25)
    e = day_energy(db, day, day_runtime(db, day))
    assert e.house_kwh == pytest.approx(24.0) and e.month_to_date_house_kwh == pytest.approx(48.0)


# ---------- weather ----------

def test_hdd_integration_method():
    temps = [50] * 12 + [75] * 12
    assert degree_days(temps, 65, "heat") == pytest.approx(7.5)   # daily-mean method would give 2.5
    assert degree_days(temps, 65, "cool") == pytest.approx(5.0)


def test_day_weather(db):
    day = date(2026, 1, 15)
    hours = store.local_day_hours(day)
    store.upsert(db, "weather_hourly", [{"ts": h, "temp_f": 30.0 + (i % 2) * 10, "wind_mph": 4.0,
                                         "source": "archive"} for i, h in enumerate(hours)])
    w = day_weather(db, day)
    assert w["hdd"] == pytest.approx((35 * 12 + 25 * 12) / 24)
    assert w["temp_min_f"] == 30 and w["temp_max_f"] == 40 and w["wind_mean_mph"] == 4.0
    assert w["source"] == "archive" and w["hdd_ecobee"] is None


def test_hdd_ecobee_hourly_then_daily_mean(db):
    day = date(2026, 1, 15)
    fill_day(db, day, {(LR, ts): {"outdoor_temp_f": 25.0} for ts in intervals(day)})
    w = day_weather(db, day)
    assert w["hdd_ecobee"] == pytest.approx(40.0) and w["hdd_ecobee_method"] == "hourly"

    day2 = date(2024, 1, 10)
    store.upsert(db, "ecobee_daily", [{"thermostat_id": str(config.BEESTAT_LR), "date": "2024-01-10",
                                       "avg_outdoor_f": 20.0}])
    w2 = day_weather(db, day2)
    assert w2["hdd_ecobee"] == pytest.approx(45.0) and w2["hdd_ecobee_method"] == "daily_mean"
    assert w2["hdd"] is None
