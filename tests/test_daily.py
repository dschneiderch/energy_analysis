from datetime import date

import pytest

from energy import config, store
from energy.daily import compute_day, format_table

from test_calc import LR, fill_carbon, fill_day, fill_hp, intervals

D = date(2025, 12, 10)


def _rates(db):
    store.upsert(db, "rates", [
        {"fuel": "electric", "schedule": "1", "effective_date": "2025-10-01", "component": "energy",
         "tier_min": 0, "tier_max": 600, "rate_per_unit": 0.10, "unit": "$/kWh"},
        {"fuel": "electric", "schedule": "1", "effective_date": "2025-10-01", "component": "energy",
         "tier_min": 600, "tier_max": None, "rate_per_unit": 0.20, "unit": "$/kWh"},
        {"fuel": "gas", "schedule": "101", "effective_date": "2022-11-01", "component": "energy",
         "tier_min": 0, "tier_max": None, "rate_per_unit": 1.03788, "unit": "$/therm"},
    ])


def _full_day(db, day=D, lr_heat_intervals=24):
    """LR compressor heat for the first N intervals (5 min each), HP 1 kWh/channel/hour,
    mains 3 kWh/hour, carbon and 30 F weather for every hour."""
    ts = intervals(day)
    fill_day(db, day, {(LR, t): {"comp_heat_s": 300.0 if i < lr_heat_intervals else 0.0,
                                 "outdoor_temp_f": 30.0} for i, t in enumerate(ts)})
    fill_hp(db, day, 1.0)
    hours = store.local_day_hours(day)
    month = [h for d in range(1, day.day) for h in store.local_day_hours(day.replace(day=d))]
    store.upsert(db, "emporia_hourly", [{"channel": config.MAINS_CHANNEL, "ts": h, "kwh": kwh,
                                         "channel_name": "mains"}
                                        for h, kwh in [(h, 3.0) for h in hours] + [(h, 0.0) for h in month]])
    fill_carbon(db, hours)
    store.upsert(db, "weather_hourly", [{"ts": h, "temp_f": 30.0, "wind_mph": 5.0, "source": "archive"}
                                        for h in hours])
    _rates(db)


def _assert_null_fields_explained(r):
    skip = {"flags", "unavailable", "notes"}
    for k, v in r.items():
        if k not in skip and v is None:
            assert k in r["unavailable"], k


def test_day_with_no_data_reports_unavailable(db):             # Review Focus 1
    r = compute_day(db, date(2020, 6, 1))
    assert r["total_kwh"] is None and "total_kwh" in r["unavailable"] and r["gco2e"] is None
    assert r["cost_usd"] is None and "cost_usd" in r["unavailable"]
    assert r["hdd"] is None and "hdd" in r["unavailable"]
    assert r["heat_h"] is None and "heat_h" in r["unavailable"]
    _assert_null_fields_explained(r)
    assert "Energy" in format_table(r)


def test_tomorrow_never_raises(db):
    from datetime import timedelta
    r = compute_day(db, date.today() + timedelta(days=1))
    _assert_null_fields_explained(r)


def test_heating_day_flag(db):
    _full_day(db, lr_heat_intervals=24)          # LR heat 2 h, no cooling
    r = compute_day(db, D)
    assert r["heating_day"] is True and r["cooling_day"] is False
    assert "heating_day" in r["flags"]
    assert r["heat_h"] == pytest.approx(2.0)


def test_full_day_values(db):
    _full_day(db)
    r = compute_day(db, D)
    hours = store.local_day_hours(D)
    assert r["total_kwh"] == pytest.approx(2.0 * len(hours))
    assert r["house_kwh"] == pytest.approx(3.0 * len(hours))
    assert r["zones"]["Living Room"]["heat"] == pytest.approx(2.0)
    assert r["gco2e"] == pytest.approx(2.0 * len(hours) * 200.0)
    assert r["low_carbon_pct"] == pytest.approx(60.0)
    assert r["hdd"] == pytest.approx(35.0)
    assert r["cost_usd"] is not None and r["cost_by_category"]
    assert set(r["unavailable"]) <= {"gas_therms", "gas_kwh_equiv", "furnace_heat_kwh", "gas_cost_usd",
                                     "gas_rate_per_therm", "gas_gco2e"}
    _assert_null_fields_explained(r)
    table = format_table(r)
    for group in ("Energy", "Runtime", "Weather", "Carbon", "Cost", "Gas", "Flags"):
        assert group in table


def test_cost_uses_house_minus_total(db):
    """R8: rest of house = mains − (heat pump + strips); prior = month-to-date mains."""
    _full_day(db)
    n = len(store.local_day_hours(D))
    # Month-to-date mains: Dec 1-9 at 3 kWh/h (full days, no DST change)
    for d in range(1, 10):
        hrs = store.local_day_hours(date(2025, 12, d))
        store.upsert(db, "emporia_hourly", [{"channel": config.MAINS_CHANNEL, "ts": h, "kwh": 3.0,
                                             "channel_name": "mains"} for h in hrs])
    r = compute_day(db, D)
    prior = 3.0 * 24 * 9                       # 648 > 600: all HVAC at tier 2
    assert r["month_to_date_house_kwh"] == pytest.approx(prior)
    assert r["cost_usd"] == pytest.approx(2.0 * n * 0.20)


def test_not_home_overlapping_ranges(db):
    store.upsert(db, "not_home", [
        {"start_date": "2025-12-01", "end_date": "2025-12-12", "source": "exclude_dates"},
        {"start_date": "2025-12-09", "end_date": "2025-12-11", "source": "ecobee_vacation"}])
    assert compute_day(db, D)["not_home"] is True
    assert "not_home" in compute_day(db, D)["flags"]
    assert compute_day(db, date(2025, 12, 13))["not_home"] is False


def test_gas_day_and_electric_unavailable(db):
    _rates(db)
    store.upsert(db, "gas_daily", [{"date": "2022-12-15", "therms": 2.0, "ccf": 2.0, "in_town": 1}])
    r = compute_day(db, date(2022, 12, 15))
    assert r["gas_therms"] == 2.0
    assert r["furnace_heat_kwh"] == pytest.approx(2 * 29.3071 * 0.95)
    assert r["gas_cost_usd"] == pytest.approx(2 * 1.03788)
    assert r["total_kwh"] is None and "2023-04-15" in r["unavailable"]["total_kwh"]
    assert r["gco2e"] is None and r["unavailable"]["gco2e"]
    _assert_null_fields_explained(r)
    table = format_table(r)
    assert "2023-04-15" in table and "therms" in table


def test_heating_day_without_thermostat_uses_hdd(db):
    hours = store.local_day_hours(date(2022, 1, 10))
    store.upsert(db, "weather_hourly", [{"ts": h, "temp_f": 40.0, "wind_mph": 1.0, "source": "archive"}
                                        for h in hours])
    r = compute_day(db, date(2022, 1, 10))
    assert r["heating_day"] is True and r["cooling_day"] is False


def test_rate_fallback_note(db):
    _full_day(db, day=date(2027, 1, 10))
    r = compute_day(db, date(2027, 1, 10))
    assert r["cost_usd"] is not None
    assert any("fallback" in n for n in r["notes"])


def test_month_to_date_unavailable_before_emporia(db):
    r = compute_day(db, date(2020, 6, 1))          # first of month: empty prior range
    assert r["month_to_date_house_kwh"] is None and "month_to_date_house_kwh" in r["unavailable"]
