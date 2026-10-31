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
