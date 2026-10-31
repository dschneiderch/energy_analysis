from datetime import date

from energy.calc.rates import rate_on
from energy.sources import local_files


def test_rate_on_known_dates(db):
    local_files.sync(db)
    r = rate_on(db, "electric", "1", date(2025, 12, 15))
    assert r.tiers == [(0, 600, 0.09641), (600, None, 0.10863)] and r.basic == 20.0
    assert r.tax_adj == 0 and r.is_fallback is False
    g = rate_on(db, "gas", "101", date(2021, 12, 15))
    assert g.tiers[0][2] == 0.72905
    assert rate_on(db, "gas", "101", date(2022, 12, 15)).tiers[0][2] == 1.03788
    assert rate_on(db, "electric", "1", date(2026, 9, 1)).tax_adj == 0.000923
    assert rate_on(db, "gas", "101", date(2026, 2, 1)).tax_adj == 0.010802


def test_rate_outside_table(db):
    local_files.sync(db)
    assert rate_on(db, "electric", "1", date(2015, 1, 1)) is None
    assert rate_on(db, "electric", "1", date(2030, 1, 1)).is_fallback is True
    assert rate_on(db, "electric", "1", date(2027, 6, 1)).is_fallback is False
