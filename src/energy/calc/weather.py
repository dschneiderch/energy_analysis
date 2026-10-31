"""Degree days and daily weather summary (spec §7.4)."""
from datetime import date

from energy import config, store

_MIN_HOURS = 12


def degree_days(temps_f: list[float], base: float, kind: str) -> float:
    """Integration method over hourly temps: Σ max(0, base − T)/24 (heat) or Σ max(0, T − base)/24 (cool)."""
    if kind == "heat":
        return sum(max(0.0, base - t) for t in temps_f) / 24
    if kind == "cool":
        return sum(max(0.0, t - base) for t in temps_f) / 24
    raise ValueError(f"kind must be 'heat' or 'cool', not {kind!r}")


def _scaled_dd(temps: list[float], n_hours: int, base: float, kind: str) -> float:
    """Degree days, scaled up to the full day when some hours are missing."""
    return degree_days(temps, base, kind) * n_hours / len(temps)


def _ecobee_hdd(conn, day: date, hours: list[str]) -> tuple[float | None, str | None]:
    start, end = store.local_day_bounds(day)
    rows = conn.execute(
        "SELECT substr(ts, 1, 13) AS h, AVG(outdoor_temp_f) FROM ecobee_interval "
        "WHERE thermostat_id=? AND ts>=? AND ts<? AND outdoor_temp_f IS NOT NULL GROUP BY h",
        (config.LR_ECOBEE_ID, start, end)).fetchall()
    if len(rows) >= config.COMPLETE_FRACTION * len(hours):
        return _scaled_dd([r[1] for r in rows], len(hours), config.HDD_BASE_F, "heat"), "hourly"
    main = config.BEESTAT_OLD if day < config.LR_BEESTAT_START else config.BEESTAT_LR
    for tid in (main, config.BEESTAT_UP):
        r = conn.execute("SELECT avg_outdoor_f FROM ecobee_daily WHERE thermostat_id=? AND date=?",
                         (str(tid), day.isoformat())).fetchone()
        if r is not None and r[0] is not None:
            return max(0.0, config.HDD_BASE_F - r[0]), "daily_mean"
    return None, None


def day_weather(conn, day: date) -> dict:
    hours = store.local_day_hours(day)
    start, end = store.local_day_bounds(day)
    rows = conn.execute("SELECT temp_f, wind_mph, source FROM weather_hourly WHERE ts>=? AND ts<?",
                        (start, end)).fetchall()
    temps = [r[0] for r in rows if r[0] is not None]
    winds = [r[1] for r in rows if r[1] is not None]
    hdd_e, method = _ecobee_hdd(conn, day, hours)
    out = {"hdd": None, "cdd": None, "temp_mean_f": None, "temp_min_f": None, "temp_max_f": None,
           "wind_mean_mph": None, "hdd_ecobee": hdd_e, "hdd_ecobee_method": method, "source": None,
           "reason": None, "notes": []}
    if len(temps) < _MIN_HOURS:
        out["reason"] = f"only {len(temps)} of {len(hours)} hourly temperatures"
        return out
    out.update(
        hdd=_scaled_dd(temps, len(hours), config.HDD_BASE_F, "heat"),
        cdd=_scaled_dd(temps, len(hours), config.CDD_BASE_F, "cool"),
        temp_mean_f=sum(temps) / len(temps), temp_min_f=min(temps), temp_max_f=max(temps),
        wind_mean_mph=sum(winds) / len(winds) if winds else None,
        source="+".join(sorted({r[2] for r in rows if r[2]})) or None,
    )
    if len(temps) < len(hours):
        out["notes"].append(f"{len(hours) - len(temps)} hourly temperatures missing; degree days scaled")
    return out
