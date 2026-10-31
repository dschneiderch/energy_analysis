import pytest
from datetime import date

from energy import config
from energy.sources import local_files
from energy.sources.local_files import parse_exclude_dates


def test_parse_exclude_dates():
    txt = "# c\n2021-12-23 -- 2022-1-17\n2025-8-16 -- 2025-8-17\n"
    assert parse_exclude_dates(txt) == [(date(2021, 12, 23), date(2022, 1, 17)),
                                        (date(2025, 8, 16), date(2025, 8, 17))]


def test_parse_exclude_single_date_and_inline_comment():
    assert parse_exclude_dates("2024-3-5  # trip\n\n") == [(date(2024, 3, 5), date(2024, 3, 5))]


def test_exclude_dates_resync_adds_and_removes(db, tmp_path, monkeypatch):
    f = tmp_path / "exclude_dates.txt"
    monkeypatch.setattr(config, "EXCLUDE_DATES_FILE", f)
    f.write_text("2025-8-16 -- 2025-8-17\n")
    local_files.sync(db)
    f.write_text("2026-11-20 -- 2026-11-27\n")
    local_files.sync(db)
    rows = db.execute("select start_date, end_date from not_home where source='exclude_dates'").fetchall()
    assert [tuple(r) for r in rows] == [("2026-11-20", "2026-11-27")]


def test_gas_files_read_from_configured_dir(db, tmp_path, monkeypatch):
    (tmp_path / "gas_furnace_therms_history.csv").write_text(
        "Date,Therms,HDD – 65F,In Town\n2022-12-15,3.5,40,1\n")
    (tmp_path / "avista-gas-daily-x.csv").write_text(
        'Name, X\nFuel Type,Gas\n,\nMeter Badge Number: 1,\nDate/Time,CCFC  \n'
        '2022-12-15 15:59:22,2\n2022-12-16 15:59:22,4\n')
    monkeypatch.setattr(config, "GAS_DIR", tmp_path)
    local_files.sync(db)
    assert db.execute("select therms from gas_daily where date='2022-12-15'").fetchone()[0] == 3.5
    rows = db.execute("select date, therms, ccf, in_town from gas_daily order by date").fetchall()
    assert [tuple(r) for r in rows] == [("2022-12-15", 3.5, 2.0, 1), ("2022-12-16", None, 4.0, None)]


def _rates_file(tmp_path, monkeypatch, body):
    f = tmp_path / "rates.csv"
    f.write_text("fuel,schedule,effective_date,component,tier_min,tier_max,rate_per_unit,unit\n" + body)
    monkeypatch.setattr(config, "RATES_CSV", f)
    return f


def test_rates_resync_replaces_stale_rows(db, tmp_path, monkeypatch):
    f = _rates_file(tmp_path, monkeypatch, "electric,1,2021-09-01,energy,0,,0.08,$/kWh\n")
    local_files._load_rates(db)
    f.write_text(f.read_text().replace("2021-09-01", "2021-10-01"))
    local_files._load_rates(db)
    assert [r[0] for r in db.execute("select effective_date from rates")] == ["2021-10-01"]


def test_rates_date_without_energy_component_raises(db, tmp_path, monkeypatch):
    _rates_file(tmp_path, monkeypatch, "electric,1,2021-09-01,energy,0,,0.08,$/kWh\n"
                                       "electric,1,2022-01-01,basic,0,,6,$/month\n")
    with pytest.raises(ValueError, match="2022-01-01"):
        local_files._load_rates(db)


def test_gas_blank_date_skipped_and_cleaned(db, tmp_path, monkeypatch):
    (tmp_path / "gas_furnace_therms_history.csv").write_text("Date,Therms,In Town\n2025-01-01,3.5,1\n,,\n")
    monkeypatch.setattr(config, "GAS_DIR", tmp_path)
    db.execute("insert into gas_daily (date) values ('')")
    local_files._load_gas(db)
    assert [r[0] for r in db.execute("select date from gas_daily")] == ["2025-01-01"]


def test_parse_exclude_dates_errors_name_line():
    with pytest.raises(ValueError, match=r"line 2.*2025-5-3 -- 2025-5-1"):
        parse_exclude_dates("2025-1-1\n2025-5-3 -- 2025-5-1\n")
    with pytest.raises(ValueError, match=r"line 2.*garbage"):
        parse_exclude_dates("2025-1-1\ngarbage\n")
