"""Avista rate lookup."""
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class RateSet:
    effective_date: date
    tiers: list[tuple[float, float | None, float]]
    tax_adj: float
    basic: float
    is_fallback: bool


def rate_on(conn, fuel: str, schedule: str, day: date) -> RateSet | None:
    row = conn.execute(
        "SELECT MAX(effective_date) FROM rates WHERE fuel=? AND schedule=? AND effective_date<=?",
        (fuel, schedule, day.isoformat())).fetchone()
    if row[0] is None:
        return None
    eff = row[0]
    rows = conn.execute(
        "SELECT component, tier_min, tier_max, rate_per_unit FROM rates "
        "WHERE fuel=? AND schedule=? AND effective_date=? ORDER BY tier_min",
        (fuel, schedule, eff)).fetchall()
    tiers = [(r["tier_min"], r["tier_max"], r["rate_per_unit"]) for r in rows if r["component"] == "energy"]
    tax = sum(r["rate_per_unit"] or 0 for r in rows if r["component"] == "tax_adj")
    basic = sum(r["rate_per_unit"] or 0 for r in rows if r["component"] == "basic")
    newest = date.fromisoformat(conn.execute(
        "SELECT MAX(effective_date) FROM rates WHERE fuel=? AND schedule=?", (fuel, schedule)).fetchone()[0])
    return RateSet(date.fromisoformat(eff), tiers, float(tax), float(basic),
                   day > newest + timedelta(days=365))
