"""Daily heat pump / strip energy by category (spec §7.3)."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import median

from energy import config, store
from energy.calc.runtime import Runtime

_FMT = "%Y-%m-%dT%H:%M:%SZ"
CATEGORIES = ("heating", "cooling", "other")


@dataclass
class Energy:
    heating_kwh: float | None
    cooling_kwh: float | None
    other_kwh: float | None
    strip_kwh: float | None
    total_kwh: float | None
    hourly_kwh: dict[str, float] = field(default_factory=dict)
    hourly_category: dict[str, dict[str, float]] = field(default_factory=dict)
    strip_crosscheck_mismatch: bool = False
    house_kwh: float | None = None
    month_to_date_house_kwh: float | None = None
    hp_kwh: float | None = None                     # channels 7+8 only (no strips)
    strip_crosscheck_extra_kwh: float | None = None
    reason: str | None = None                       # why kWh fields are None
    notes: list[str] = field(default_factory=list)


def _channel_hours(conn, channel: str, start: str, end: str) -> dict[str, float]:
    return {r[0]: (r[1] or 0.0) for r in conn.execute(
        "SELECT ts, kwh FROM emporia_hourly WHERE channel=? AND ts>=? AND ts<?", (channel, start, end))}


def _full_sum(conn, channel: str, hours: list[str]) -> float | None:
    """Sum of a channel over the given hours, or None if any hour is missing."""
    if not hours:
        return 0.0
    vals = _channel_hours(conn, channel, hours[0], _next_hour(hours[-1]))
    if any(h not in vals for h in hours):
        return None
    return sum(vals[h] for h in hours)


def _next_hour(ts: str, hours: int = 1) -> str:
    return (datetime.strptime(ts, _FMT).replace(tzinfo=timezone.utc) + timedelta(hours=hours)).strftime(_FMT)


def _hours_between(start_day: date, end_day: date) -> list[str]:
    out: list[str] = []
    d = start_day
    while d < end_day:
        out += store.local_day_hours(d)
        d += timedelta(days=1)
    return out


def _unmonitored(conn, hours: list[str]) -> dict[str, float]:
    """mains − Σ monitored circuits (numeric, non-virtual channels) per hour, where mains exists."""
    out = {}
    virtual = set(config.VIRTUAL_CHANNELS)
    for h in hours:
        rows = conn.execute("SELECT channel, kwh FROM emporia_hourly WHERE ts=?", (h,)).fetchall()
        mains = None
        circuits = 0.0
        for ch, kwh in rows:
            if ch == config.MAINS_CHANNEL:
                mains = kwh
            elif ch.isdigit() and ch not in virtual:
                circuits += kwh or 0.0
        if mains is not None:
            out[h] = mains - circuits
    return out


def _strip_crosscheck(conn, hourly_strip: dict[str, float]) -> float | None:
    """Measured extra unmonitored kWh in strip hours vs. a 14-day same-hour median baseline."""
    extra = 0.0
    checked = False
    for h, strip in hourly_strip.items():
        if strip <= 0:
            continue
        now = _unmonitored(conn, [h]).get(h)
        prior = [_next_hour(h, -24 * k) for k in range(1, 15)]
        base = list(_unmonitored(conn, prior).values())
        if now is None or not base:
            continue
        extra += now - median(base)
        checked = True
    return extra if checked else None


def _unavailable(reason: str, **kw) -> Energy:
    return Energy(None, None, None, None, None, reason=reason, **kw)


def day_energy(conn, day: date, rt: Runtime) -> Energy:
    hours = store.local_day_hours(day)
    start, end = store.local_day_bounds(day)
    house = _full_sum(conn, config.MAINS_CHANNEL, hours)
    month_start = day.replace(day=1)
    mtd = _full_sum(conn, config.MAINS_CHANNEL, _hours_between(month_start, day))
    extra = {"house_kwh": house, "month_to_date_house_kwh": mtd}

    if day < config.HP_START_DATE:
        return _unavailable(f"no Emporia data before {config.HP_START_DATE.isoformat()}", **extra)
    chans = [_channel_hours(conn, ch, start, end) for ch in config.HP_CHANNELS]
    missing = [h for h in hours if any(h not in c for c in chans)]
    if missing:
        return _unavailable(
            f"Emporia channel 7/8 missing {len(missing)} of {len(hours)} hours", **extra)
    hp = {h: sum(c[h] for c in chans) for h in hours}
    hp_total = sum(hp.values())

    if rt.basis is None:
        e = Energy(None, None, None, None, hp_total, dict(hp), {}, hp_kwh=hp_total,
                   reason="no thermostat runtime to assign kWh to heating/cooling/other", **extra)
        return e

    notes: list[str] = []
    strip = {h: 0.0 for h in hours}
    cats: dict[str, dict[str, float]] = {}
    post_strip = day >= config.STRIP_INSTALL_DATE
    if rt.basis == "5min":
        for h in hours:
            r = rt.hourly.get(h, {"heat_s": 0.0, "cool_s": 0.0, "aux_s": 0.0})
            if post_strip:
                strip[h] = r["aux_s"] / 3600 * config.STRIP_KW
            heat_rt = r["heat_s"] + r["aux_s"]
            cool_rt = r["cool_s"]
            if heat_rt > 0 or cool_rt > 0:
                frac = heat_rt / (heat_rt + cool_rt)
                c = {"heating": hp[h] * frac, "cooling": hp[h] * (1 - frac), "other": 0.0}
            else:
                c = {"heating": 0.0, "cooling": 0.0, "other": hp[h]}
            c["heating"] += strip[h]
            cats[h] = c
    else:  # daily lower bound: whole day goes to one category
        if rt.heat_h * 3600 > config.CALL_MIN_SECONDS:
            cat = "heating"
        elif rt.cool_h * 3600 > config.CALL_MIN_SECONDS:
            cat = "cooling"
        else:
            cat = "other"
        day_strip = (rt.aux_h or 0.0) * config.STRIP_KW if post_strip else 0.0
        if day_strip:
            notes.append("strip kWh spread evenly over the day (daily runtime only)")
        for h in hours:
            strip[h] = day_strip / len(hours)
            c = {k: 0.0 for k in CATEGORIES}
            c[cat] = hp[h]
            c["heating"] += strip[h]
            cats[h] = c

    strip_total = sum(strip.values())
    totals = {k: sum(c[k] for c in cats.values()) for k in CATEGORIES}
    hourly_kwh = {h: hp[h] + strip[h] for h in hours}

    mismatch = False
    measured = None
    if strip_total > 0 and rt.basis == "5min":
        measured = _strip_crosscheck(conn, strip)
        lo, hi = config.STRIP_RATIO_BAND
        if measured is not None and not (lo * strip_total <= measured <= hi * strip_total):
            mismatch = True

    return Energy(totals["heating"], totals["cooling"], totals["other"], strip_total,
                  hp_total + strip_total, hourly_kwh, cats, mismatch, house, mtd,
                  hp_kwh=hp_total, strip_crosscheck_extra_kwh=measured, notes=notes)
