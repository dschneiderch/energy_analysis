"""Daily HVAC runtime from ecobee 5-min intervals or beestat daily sums (spec §7.1-§7.2)."""
from dataclasses import dataclass, field
from datetime import date

from energy import config, store


@dataclass
class Runtime:
    basis: str | None                    # "5min", "daily_lower_bound" or None
    heat_h: float | None
    cool_h: float | None
    aux_h: float | None
    furnace_h: float | None
    zones: dict[str, dict[str, float]] = field(default_factory=dict)
    hourly: dict[str, dict[str, float]] = field(default_factory=dict)
    reason: str | None = None            # why runtime is unavailable, when basis is None


def _hour(ts: str) -> str:
    return ts[:13] + ":00:00Z"


def _five_min(conn, day: date) -> Runtime | None:
    start, end = store.local_day_bounds(day)
    expected = len(store.local_day_hours(day)) * 12
    rows = conn.execute(
        "SELECT thermostat_id, ts, comp_heat_s, comp_cool_s, aux_heat_s FROM ecobee_interval "
        "WHERE thermostat_id IN (?, ?) AND ts >= ? AND ts < ?",
        (config.LR_ECOBEE_ID, config.UP_ECOBEE_ID, start, end)).fetchall()
    by_tid: dict[str, dict[str, tuple[float, float, float]]] = {config.LR_ECOBEE_ID: {}, config.UP_ECOBEE_ID: {}}
    for r in rows:
        by_tid[r[0]][r[1]] = (r[2] or 0.0, r[3] or 0.0, r[4] or 0.0)
    if any(len(v) < config.COMPLETE_FRACTION * expected for v in by_tid.values()):
        return None

    pre_strip = day < config.STRIP_INSTALL_DATE
    lr, up = by_tid[config.LR_ECOBEE_ID], by_tid[config.UP_ECOBEE_ID]
    zones = {"LR": {"heat": 0.0, "cool": 0.0, "aux": 0.0}, "UP": {"heat": 0.0, "cool": 0.0, "aux": 0.0}}
    hourly: dict[str, dict[str, float]] = {}
    for ts in sorted(set(lr) | set(up)):
        lr_heat, lr_cool, lr_aux = lr.get(ts, (0.0, 0.0, 0.0))
        _, up_cool, up_aux = up.get(ts, (0.0, 0.0, 0.0))
        lr_heat_total = lr_heat + (lr_aux if pre_strip else 0.0)
        aux_s = 0.0 if pre_strip else lr_aux
        zones["LR"]["heat"] += lr_heat_total
        zones["LR"]["cool"] += lr_cool
        zones["LR"]["aux"] += aux_s
        zones["UP"]["heat"] += up_aux          # Upstairs logs compressor heat as aux (spec §2)
        zones["UP"]["cool"] += up_cool
        h = hourly.setdefault(_hour(ts), {"heat_s": 0.0, "cool_s": 0.0, "aux_s": 0.0})
        h["heat_s"] += max(lr_heat_total, up_aux)
        h["cool_s"] += max(lr_cool, up_cool)
        h["aux_s"] += aux_s

    zones = {z: {k: v / 3600 for k, v in d.items()} for z, d in zones.items()}
    heat = sum(h["heat_s"] for h in hourly.values()) / 3600
    cool = sum(h["cool_s"] for h in hourly.values()) / 3600
    aux = sum(h["aux_s"] for h in hourly.values()) / 3600
    if day < config.HP_START_DATE:
        return Runtime("5min", 0.0, cool, aux, heat, zones, hourly)
    return Runtime("5min", heat, cool, aux, 0.0, zones, hourly)


def _daily_row(conn, beestat_id: int, day: date):
    return conn.execute(
        "SELECT comp_heat_s, comp_cool_s, aux_heat_s FROM ecobee_daily WHERE thermostat_id=? AND date=?",
        (str(beestat_id), day.isoformat())).fetchone()


def _daily(conn, day: date) -> Runtime | None:
    main_key, main_id = ("OLD", config.BEESTAT_OLD) if day < config.LR_BEESTAT_START else ("LR", config.BEESTAT_LR)
    main = _daily_row(conn, main_id, day)
    up = _daily_row(conn, config.BEESTAT_UP, day)
    if main is None and up is None:
        return None
    m_heat, m_cool, m_aux = (v or 0.0 for v in (main or (0, 0, 0)))
    u_cool, u_aux = ((up[1] or 0.0), (up[2] or 0.0)) if up else (0.0, 0.0)
    pre_strip = day < config.STRIP_INSTALL_DATE
    m_heat_total = m_heat + (m_aux if pre_strip else 0.0)
    aux_s = 0.0 if pre_strip else m_aux
    zones = {}
    if main is not None:
        zones[main_key] = {"heat": m_heat_total / 3600, "cool": m_cool / 3600, "aux": aux_s / 3600}
    if up is not None:
        zones["UP"] = {"heat": u_aux / 3600, "cool": u_cool / 3600, "aux": 0.0}
    heat = max(m_heat_total, u_aux) / 3600
    cool = max(m_cool, u_cool) / 3600
    aux = aux_s / 3600
    if day < config.HP_START_DATE:
        return Runtime("daily_lower_bound", 0.0, cool, aux, heat, zones, {})
    return Runtime("daily_lower_bound", heat, cool, aux, 0.0, zones, {})


def day_runtime(conn, day: date) -> Runtime:
    rt = _five_min(conn, day) or _daily(conn, day)
    if rt is not None:
        return rt
    return Runtime(None, None, None, None, None, {}, {}, reason="no ecobee or beestat runtime for this day")
