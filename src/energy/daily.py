"""One day's report, computed from the store only (spec §7.8-§7.9)."""
from datetime import date

from energy import config
from energy.calc.carbon import day_carbon
from energy.calc.cost import electric_cost, gas_day
from energy.calc.energy import day_energy
from energy.calc.rates import rate_on
from energy.calc.runtime import day_runtime
from energy.calc.weather import day_weather

ZONE_NAMES = {"LR": "Living Room", "UP": "Upstairs", "OLD": "Old main thermostat"}
ENERGY_KEYS = ("heating_kwh", "cooling_kwh", "other_kwh", "strip_kwh", "total_kwh", "hp_kwh")
RUNTIME_KEYS = ("heat_h", "cool_h", "aux_h", "furnace_h")
WEATHER_KEYS = ("hdd", "cdd", "temp_mean_f", "temp_min_f", "temp_max_f", "wind_mean_mph")
CARBON_KEYS = ("gco2e", "gco2e_by_category", "low_carbon_pct")
GAS_KEYS = ("gas_therms", "gas_kwh_equiv", "furnace_heat_kwh", "gas_cost_usd", "gas_rate_per_therm", "gas_gco2e")

_META = ("flags", "unavailable", "notes")


def _not_home(conn, day: date) -> bool:
    d = day.isoformat()
    return conn.execute("SELECT 1 FROM not_home WHERE start_date<=? AND end_date>=? LIMIT 1",
                        (d, d)).fetchone() is not None


def _set(r: dict, key: str, value, reason: str | None) -> None:
    r[key] = value
    if value is None:
        r["unavailable"][key] = reason or "no data"


def _energy(conn, day, rt, r):
    e = day_energy(conn, day, rt)
    for k in ENERGY_KEYS:
        _set(r, k, getattr(e, k), e.reason)
    if day < config.HP_START_DATE:     # an empty prior range would otherwise sum to 0.0
        e.house_kwh = e.month_to_date_house_kwh = None
    _set(r, "house_kwh", e.house_kwh,
         e.reason if day < config.HP_START_DATE else "Emporia mains missing hours for this day")
    _set(r, "month_to_date_house_kwh", e.month_to_date_house_kwh,
         e.reason if day < config.HP_START_DATE else "Emporia mains missing hours earlier this month")
    r["strip_crosscheck"] = {"checked": e.strip_crosscheck_extra_kwh is not None,
                             "measured_extra_kwh": e.strip_crosscheck_extra_kwh,
                             "mismatch": e.strip_crosscheck_mismatch}
    if e.strip_crosscheck_mismatch:
        r["flags"].append("strip_crosscheck_mismatch")
    r["notes"] += e.notes
    return e


def _runtime(rt, r):
    r["runtime_basis"] = rt.basis
    for k in RUNTIME_KEYS:
        _set(r, k, getattr(rt, k), rt.reason)
    zones = {ZONE_NAMES.get(z, z): v for z, v in rt.zones.items()} if rt.basis else None
    _set(r, "zones", zones, rt.reason)
    if rt.basis is None:
        r["unavailable"]["runtime_basis"] = rt.reason
    elif rt.basis == "5min":
        r["notes"].append("runtime from ecobee 5-minute intervals")
    else:
        r["notes"].append("runtime from beestat daily sums (zone overlap unknown: lower bound)")
        r["flags"].append("runtime_lower_bound")


def _weather(conn, day, r):
    w = day_weather(conn, day)
    for k in WEATHER_KEYS:
        _set(r, k, w[k], w["reason"] or "no wind data")
    _set(r, "hdd_ecobee", w["hdd_ecobee"], "no ecobee or beestat outdoor temperature for this day")
    _set(r, "hdd_ecobee_method", w["hdd_ecobee_method"], "no ecobee or beestat outdoor temperature for this day")
    _set(r, "weather_source", w["source"], w["reason"])
    if w["source"] and "forecast" in w["source"]:
        r["notes"].append("weather includes Open-Meteo forecast-model hours (archive not yet available)")
    r["notes"] += w["notes"]


def _carbon(conn, day, e, r):
    hourly = e.hourly_kwh if e.total_kwh is not None else None
    c = day_carbon(conn, day, hourly, e.hourly_category or None)
    r["gco2e"], r["low_carbon_pct"] = c["gco2e"], c["low_carbon_pct"]
    by_cat = c["gco2e_by_category"] if (c["gco2e_by_category"] and e.hourly_category) else None
    for k in ("gco2e", "low_carbon_pct"):
        _set(r, k, r[k], c["reason"])
    _set(r, "gco2e_by_category", by_cat, c["reason"] or e.reason)
    r["notes"] += c["notes"]


def _cost(conn, day, e, r):
    if e.total_kwh is None:
        for k in ("cost_usd", "cost_by_category", "electric_rate_effective"):
            _set(r, k, None, e.reason)
        return
    rs = rate_on(conn, "electric", "1", day)
    if rs is None:
        for k in ("cost_usd", "cost_by_category", "electric_rate_effective"):
            _set(r, k, None, "no electric rate in effect for this date")
        return
    by_cat = ({"heating": e.heating_kwh, "cooling": e.cooling_kwh, "other": e.other_kwh}
              if e.heating_kwh is not None else {"unassigned": e.total_kwh})
    # Mains already includes the (unmetered) strips, so the rest of the house is mains − total.
    rest = (e.house_kwh - e.total_kwh) if e.house_kwh is not None else 0.0
    prior = e.month_to_date_house_kwh if e.month_to_date_house_kwh is not None else 0.0
    if e.house_kwh is None or e.month_to_date_house_kwh is None:
        r["notes"].append("tier placement approximate: house mains kWh incomplete (missing hours counted as 0)")
    out = electric_cost(rs, prior, rest, by_cat)
    r["cost_usd"] = out["cost_usd"]
    _set(r, "cost_by_category", out["cost_by_category"] if e.heating_kwh is not None else None, e.reason)
    r["electric_rate_effective"] = rs.effective_date.isoformat()
    r["notes"] += out["notes"]


def _gas(conn, day, r):
    g = gas_day(conn, day)
    if g is None:
        for k in GAS_KEYS:
            _set(r, k, None, "no gas usage data for this day")
        return
    for k in GAS_KEYS:
        _set(r, k, g[k], g["reason"])
    r["notes"] += g["notes"]


def _flags(r):
    has_rt = r["runtime_basis"] is not None
    if has_rt:
        heat = sum(r[k] or 0.0 for k in ("heat_h", "furnace_h", "aux_h"))
        cool = r["cool_h"] or 0.0
        limit = config.CALL_MIN_SECONDS / 3600
        r["heating_day"] = heat > limit and cool == 0
        r["cooling_day"] = cool > limit and heat == 0
    else:
        r["heating_day"] = None if r["hdd"] is None else r["hdd"] >= config.HEATING_DAY_MIN_HDD
        r["cooling_day"] = None if r["cdd"] is None else r["cdd"] >= config.HEATING_DAY_MIN_HDD
        if r["hdd"] is None:
            r["unavailable"]["heating_day"] = "no thermostat runtime and no weather"
            r["unavailable"]["cooling_day"] = "no thermostat runtime and no weather"
        else:
            r["notes"].append("heating/cooling day from degree days (no thermostat runtime)")
    for k in ("heating_day", "cooling_day"):
        if r[k]:
            r["flags"].insert(0, k)
    if r["not_home"]:
        r["flags"].insert(0, "not_home")


def compute_day(conn, day: date) -> dict:
    r: dict = {"date": day.isoformat(), "not_home": _not_home(conn, day),
               "heating_day": None, "cooling_day": None,
               "flags": [], "unavailable": {}, "notes": []}
    rt = day_runtime(conn, day)
    e = _energy(conn, day, rt, r)
    _runtime(rt, r)
    _weather(conn, day, r)
    _carbon(conn, day, e, r)
    _cost(conn, day, e, r)
    _gas(conn, day, r)
    _flags(r)
    # Every top-level field is a value or None plus a reason.
    for k, v in r.items():
        if v is None and k not in _META and k not in r["unavailable"]:
            r["unavailable"][k] = "no data"
    # Move the bookkeeping keys to the end for readability.
    for k in _META:
        r[k] = r.pop(k)
    return r


# ---------- table ----------

_GROUPS = [
    ("Energy", [("heating_kwh", "kWh"), ("cooling_kwh", "kWh"), ("other_kwh", "kWh"), ("strip_kwh", "kWh"),
                ("hp_kwh", "kWh"), ("total_kwh", "kWh"), ("house_kwh", "kWh"),
                ("month_to_date_house_kwh", "kWh")]),
    ("Runtime", [("runtime_basis", ""), ("heat_h", "h"), ("cool_h", "h"), ("aux_h", "h"),
                 ("furnace_h", "h"), ("zones", "")]),
    ("Weather", [("hdd", "°F·day"), ("cdd", "°F·day"), ("temp_mean_f", "°F"), ("temp_min_f", "°F"),
                 ("temp_max_f", "°F"), ("wind_mean_mph", "mph"), ("hdd_ecobee", "°F·day"),
                 ("hdd_ecobee_method", ""), ("weather_source", "")]),
    ("Carbon", [("gco2e", "g CO2e"), ("low_carbon_pct", "%"), ("gco2e_by_category", "g CO2e")]),
    ("Cost", [("cost_usd", "$"), ("cost_by_category", "$"), ("electric_rate_effective", "")]),
    ("Gas", [("gas_therms", "therms"), ("gas_kwh_equiv", "kWh"), ("furnace_heat_kwh", "kWh"),
             ("gas_cost_usd", "$"), ("gas_rate_per_therm", "$/therm"), ("gas_gco2e", "g CO2e")]),
]


def _fmt(v, unit: str) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if unit == "$/therm":
            return f"${v:.5f}/therm"
        s = f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.2f}"
        return f"${s}" if unit == "$" else f"{s} {unit}".strip()
    if isinstance(v, dict):
        parts = []
        for k, x in v.items():
            if isinstance(x, dict):
                parts.append(f"{k}: " + ", ".join(f"{kk} {xx:.2f} {unit or 'h'}".strip() for kk, xx in x.items()))
            else:
                parts.append(f"{k} {_fmt(x, unit)}")
        return "; ".join(parts) if parts else "-"
    return f"{v} {unit}".strip()


def format_table(report: dict) -> str:
    lines = [f"Energy report for {report['date']}"]
    unavailable = report["unavailable"]
    width = 26
    for title, fields in _GROUPS:
        lines.append("")
        lines.append(title)
        for key, unit in fields:
            if key not in report:
                continue
            v = report[key]
            text = f"n/a ({unavailable.get(key, 'no data')})" if v is None else _fmt(v, unit)
            lines.append(f"  {key:<{width}} {text}")
    lines.append("")
    lines.append("Flags")
    for key in ("not_home", "heating_day", "cooling_day"):
        v = report[key]
        text = f"n/a ({unavailable.get(key, 'no data')})" if v is None else _fmt(v, "")
        lines.append(f"  {key:<{width}} {text}")
    sc = report.get("strip_crosscheck") or {}
    if sc.get("checked"):
        lines.append(f"  {'strip_crosscheck':<{width}} measured extra {sc['measured_extra_kwh']:.2f} kWh"
                     f"{' (MISMATCH)' if sc['mismatch'] else ''}")
    other = [f for f in report["flags"] if f not in ("not_home", "heating_day", "cooling_day")]
    if other:
        lines.append(f"  {'other':<{width}} {', '.join(other)}")
    if report["notes"]:
        lines.append("")
        lines.append("Notes")
        lines += [f"  - {n}" for n in report["notes"]]
    return "\n".join(lines)
