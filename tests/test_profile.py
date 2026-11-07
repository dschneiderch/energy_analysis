import pytest

from energy import config
from energy.calc import profile as prof

LR, UP = config.LR_ECOBEE_ID, config.UP_ECOBEE_ID
T0 = 1_767_225_600  # 2026-01-01T00:00:00Z


def _off_rows(steps, outdoor=lambda k: 30.0, event=lambda k: None):
    """Both thermostats off; Living Room indoor falls 0.1 °F per 5 minutes from 70 °F."""
    rows = {}
    for k in range(steps):
        t = T0 + k * 300
        rows[t] = {tid: {"ts": t, "indoor": 70 - 0.1 * k, "outdoor": outdoor(k), "heat_1": 0,
                         "auxiliary_heat_1": 0, "cool_1": 0, "event": event(k), "climate": "Home"}
                   for tid in (LR, UP)}
    return rows


def test_resist_sample_ends_when_outdoor_temperature_changes():
    rows = _off_rows(36, outdoor=lambda k: 30.0 if k < 24 else 31.0)
    s = prof.samples(rows, LR, T0, T0 + 35 * 300)
    # Starts at the first all-off row; ends one step before the outdoor change (23 steps = 115 min).
    assert s["resist"][30] == [pytest.approx(-1.2)]


def test_comfort_setting_change_drops_the_last_30_minutes():
    rows = _off_rows(36, event=lambda k: None if k < 12 else "Vacation")
    s = prof.samples(rows, LR, T0, T0 + 35 * 300)
    # Change at step 12: the end moves back 30 min to step 6 (30 min sample, still >= the 30 min minimum).
    assert s["resist"][30] == [pytest.approx(-1.2)]


def test_resist_needs_every_thermostat_off():
    rows = _off_rows(36, outdoor=lambda k: 30.0 if k < 24 else 31.0)
    for k in range(10, 12):
        rows[T0 + k * 300][UP]["cool_1"] = 300
    s = prof.samples(rows, LR, T0, T0 + 35 * 300)
    # Split around the Upstairs cooling: steps 0-9 and 12-23 (45 and 55 minutes).
    assert s["resist"] == {30: [pytest.approx(-1.2), pytest.approx(-1.2)]}


def test_profile_bins_medians_and_drops_outliers():
    binned = {k: {} for k in prof.TYPES}
    binned["resist"] = {x: [0.03 * x - 2, 0.03 * x - 2] for x in range(20, 30)}
    binned["resist"][30] = [5.0, 5.0]   # outlier bin
    binned["resist"][31] = [1.0]        # one sample: not enough for a bin
    p = prof.profile(binned, "compressor")
    assert sorted(p["temperature"]["resist"]["deltas"]) == list(range(20, 30))
    assert p["temperature"]["resist"]["linear_trendline"] == {"slope": 0.03, "intercept": -2.0}
    assert p["balance_point"]["resist"] == pytest.approx(66.7)
    assert p["temperature"]["heat_1"] is None  # fewer than 5 bins


def test_php_round_is_half_away_from_zero():
    assert prof._php_round(32.5) == 33 and prof._php_round(-32.5) == -33 and prof._php_round(0.125, 2) == 0.13


def test_dates_are_whole_local_days_and_datetimes_pass_through():
    from datetime import date, datetime, timezone
    assert prof._instant(date(2026, 1, 1)) == datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
    assert prof._instant(date(2026, 7, 1), next_day=True) == datetime(2026, 7, 2, 7, tzinfo=timezone.utc)
    exact = datetime(2026, 10, 31, 20, 50, tzinfo=timezone.utc)
    assert prof._instant(exact, next_day=True) is exact
