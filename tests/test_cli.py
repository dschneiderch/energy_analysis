import json
from datetime import date

import pytest

from energy import cli, store
from energy.cli import SOURCES
from energy.sources import ecobee
from energy.sources.ecobee import EcobeeLoginRequired


def raise_(exc):
    def f(*a, **k):
        raise exc
    return f


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    path = tmp_path / "energy.sqlite"
    monkeypatch.setattr(cli, "DB_PATH", path)
    return path


def test_sources_order():
    assert list(SOURCES) == ["local", "emporia", "ha_carbon", "weather", "beestat", "ecobee"]


def test_sync_continues_after_ecobee_login_failure(monkeypatch, capsys, tmp_db):   # Review Focus 4
    monkeypatch.setattr(ecobee, "sync", raise_(EcobeeLoginRequired("run `energy ecobee-login`")))
    calls = []
    for name in ["local", "emporia", "ha_carbon", "weather", "beestat"]:
        monkeypatch.setattr(SOURCES[name], "sync", lambda conn, since=None, n=name: calls.append(n) or 0)
    assert cli.main(["sync"]) == 1
    out = capsys.readouterr().out
    assert "ecobee: FAILED" in out and "energy ecobee-login" in out
    assert calls == ["local", "emporia", "ha_carbon", "weather", "beestat"]


def test_sync_failure_in_middle_and_selected_sources(monkeypatch, capsys, tmp_db):
    calls = []
    for name in SOURCES:
        monkeypatch.setattr(SOURCES[name], "sync",
                            lambda conn, since=None, n=name: calls.append((n, since)) or 3)
    monkeypatch.setattr(SOURCES["emporia"], "sync", raise_(ValueError("boom")))
    assert cli.main(["sync", "weather", "emporia", "--since", "2026-01-01"]) == 1
    out = capsys.readouterr().out
    assert "emporia: FAILED: ValueError: boom" in out
    assert "Traceback" not in out
    assert calls == [("weather", date(2026, 1, 1))]     # canonical order: emporia before weather


def test_sync_all_ok(monkeypatch, capsys, tmp_db):
    for name in SOURCES:
        monkeypatch.setattr(SOURCES[name], "sync", lambda conn, since=None: 5)
    assert cli.main(["sync"]) == 0
    out = capsys.readouterr().out
    assert out.count("5 rows") == 6


def test_sync_unknown_source(capsys, tmp_db):
    with pytest.raises(SystemExit) as e:
        cli.main(["sync", "nope"])
    assert e.value.code == 2


def test_day_missing_db(capsys, tmp_db):           # R3: exit 1
    assert cli.main(["day", "2026-01-15"]) == 1
    assert 'database not found — run "energy sync"' in capsys.readouterr().out
    assert not tmp_db.exists()


def test_day_invalid_date(tmp_db):
    with pytest.raises(SystemExit) as e:
        cli.main(["day", "2026-13-45"])
    assert e.value.code == 2


def test_day_json_and_table(capsys, tmp_db):
    store.connect(tmp_db).close()
    assert cli.main(["day", "2020-06-01", "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["date"] == "2020-06-01" and r["total_kwh"] is None and "total_kwh" in r["unavailable"]
    assert cli.main(["day", "2020-06-01"]) == 0
    assert "Energy" in capsys.readouterr().out


def test_ecobee_login(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(ecobee, "login_interactive", lambda: called.append(1))
    assert cli.main(["ecobee-login"]) == 0
    assert called == [1]


def test_failure_message_redacts_query_and_secrets(monkeypatch, capsys, tmp_db):
    monkeypatch.setattr(cli, "_secret_values", lambda: ["hunter2-secret"])
    for name in SOURCES:
        monkeypatch.setattr(SOURCES[name], "sync", lambda conn, since=None: 0)
    monkeypatch.setattr(SOURCES["beestat"], "sync", raise_(
        RuntimeError("bad url https://api.beestat.io/?api_key=abc123&x=1 password hunter2-secret")))
    assert cli.main(["sync"]) == 1
    out = capsys.readouterr().out
    assert "abc123" not in out and "hunter2-secret" not in out
    assert "beestat: FAILED: RuntimeError: bad url https://api.beestat.io/?<redacted>" in out


def test_ecobee_login_failure_is_one_line(monkeypatch, capsys):
    monkeypatch.setattr(ecobee, "login_interactive", raise_(RuntimeError("bad code")))
    assert cli.main(["ecobee-login"]) == 1
    assert capsys.readouterr().out.strip() == "ecobee-login: FAILED: RuntimeError: bad code"
