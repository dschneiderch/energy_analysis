"""kWh-weighted grid carbon for the day (spec §7.5)."""
from datetime import date

from energy import config, store

CARBON_START = date(2024, 2, 1)


def _unavailable(reason: str) -> dict:
    return {"gco2e": None, "gco2e_by_category": None, "low_carbon_pct": None,
            "filled_hours": [], "reason": reason, "notes": []}


def day_carbon(conn, day: date, hourly_kwh: dict[str, float] | None,
               hourly_category: dict[str, dict[str, float]] | None) -> dict:
    if day < CARBON_START:
        return _unavailable("no grid carbon data before 2024-02")
    if hourly_kwh is None:
        return _unavailable("no heat pump kWh for this day")
    hours = store.local_day_hours(day)
    start, end = store.local_day_bounds(day)
    grid = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT ts, co2_g_per_kwh, fossil_pct FROM grid_carbon_hourly "
        "WHERE ts>=? AND ts<? AND co2_g_per_kwh IS NOT NULL AND fossil_pct IS NOT NULL", (start, end))}
    if len(grid) < config.CARBON_MIN_HOURS:
        return _unavailable(f"only {len(grid)} of {len(hours)} grid carbon hours (need {config.CARBON_MIN_HOURS})")

    mean_co2 = sum(v[0] for v in grid.values()) / len(grid)
    mean_fossil = sum(v[1] for v in grid.values()) / len(grid)
    filled = [h for h in hours if h not in grid]
    intensity = {h: grid.get(h, (mean_co2, mean_fossil)) for h in hours}

    kwh = {h: hourly_kwh.get(h, 0.0) for h in hours}
    total_kwh = sum(kwh.values())
    gco2e = sum(kwh[h] * intensity[h][0] for h in hours)
    low = (sum(kwh[h] * (100 - intensity[h][1]) for h in hours) / total_kwh) if total_kwh > 0 else None

    by_cat: dict[str, float] = {}
    for h, cats in (hourly_category or {}).items():
        if h not in intensity:
            continue
        for cat, k in cats.items():
            by_cat[cat] = by_cat.get(cat, 0.0) + k * intensity[h][0]

    notes = [f"grid carbon filled with day mean for {len(filled)} hour(s)"] if filled else []
    return {"gco2e": gco2e, "gco2e_by_category": by_cat, "low_carbon_pct": low,
            "filled_hours": filled, "reason": None, "notes": notes}
