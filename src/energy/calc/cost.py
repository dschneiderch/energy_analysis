"""Electric (tiered, stacked on house use) and gas cost (spec §7.6-§7.7)."""
from datetime import date

from energy import config
from energy.calc.rates import RateSet, rate_on


def electric_cost(rates: RateSet, prior_house_kwh: float, rest_of_house_kwh: float,
                  kwh_by_category: dict[str, float]) -> dict:
    """Price the day's HVAC kWh stacked on top of month-to-date house use plus today's rest of house.

    Energy tiers are "total as billed" per-kWh rates; tax_adj is added per kWh. Basic/seasonal excluded.
    """
    base = max(0.0, prior_house_kwh) + max(0.0, rest_of_house_kwh)
    total = sum(kwh_by_category.values())
    top = base + total
    energy_cost = 0.0
    for lo, hi, rate in rates.tiers:
        hi = float("inf") if hi is None else hi
        portion = max(0.0, min(top, hi) - max(base, lo))
        energy_cost += portion * rate
    cost = energy_cost + total * rates.tax_adj
    by_cat = {k: (cost * v / total if total else 0.0) for k, v in kwh_by_category.items()}
    notes = [f"rate effective {rates.effective_date.isoformat()} used as fallback"] if rates.is_fallback else []
    return {"cost_usd": cost, "cost_by_category": by_cat, "notes": notes}


def gas_day(conn, day: date) -> dict | None:
    row = conn.execute("SELECT therms FROM gas_daily WHERE date=?", (day.isoformat(),)).fetchone()
    if row is None or row[0] is None:
        return None
    therms = row[0]
    kwh_equiv = therms * config.KWH_PER_THERM
    rs = rate_on(conn, "gas", "101", day)
    out = {"gas_therms": therms, "gas_kwh_equiv": kwh_equiv,
           "furnace_heat_kwh": kwh_equiv * config.FURNACE_AFUE,
           "gas_cost_usd": None, "gas_rate_per_therm": None,
           "gas_gco2e": therms * config.G_CO2E_PER_THERM, "reason": None, "notes": []}
    if rs is None or not rs.tiers:
        out["reason"] = "no gas rate in effect for this date"
        return out
    per_therm = rs.tiers[0][2] + rs.tax_adj
    out["gas_cost_usd"] = therms * per_therm
    out["gas_rate_per_therm"] = per_therm
    if rs.is_fallback:
        out["notes"].append(f"gas rate effective {rs.effective_date.isoformat()} used as fallback")
    return out
