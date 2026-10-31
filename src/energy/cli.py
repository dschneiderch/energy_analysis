"""`energy` command line: sync, ecobee-login, day."""
import argparse
import json
import re
import sys
from datetime import date

from energy import config, daily, http, store
from energy.config import DB_PATH
from energy.sources import beestat, ecobee, emporia, ha_carbon, local_files, weather

SOURCES = {"local": local_files, "emporia": emporia, "ha_carbon": ha_carbon,
           "weather": weather, "beestat": beestat, "ecobee": ecobee}


def _date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {s!r} (expected YYYY-MM-DD)") from None


def _secret_values() -> list[str]:
    try:
        return [v for v in config.load_secrets().values() if v and len(v) >= 4]
    except Exception:  # noqa: BLE001 - redaction must never fail the error report
        return []


def _redact(msg: str) -> str:
    msg = re.sub(r"\?\S*", "?<redacted>", msg)          # URL query strings may carry keys
    for v in _secret_values():
        msg = msg.replace(v, "<redacted>")
    return msg


def _failure_message(exc: BaseException) -> str:
    """Only the exception text, never a traceback. HttpError and EcobeeLoginRequired are already
    safe; anything else gets its type name and a redacted message."""
    if isinstance(exc, (http.HttpError, ecobee.EcobeeLoginRequired)):
        return str(exc)
    msg = _redact(str(exc))
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def _sync(names: list[str], since: date | None) -> int:
    conn = store.connect(DB_PATH)
    failed = False
    try:
        for name in [n for n in SOURCES if n in names]:
            fn = SOURCES[name].sync
            try:
                n = fn(conn) if since is None else fn(conn, since=since)
                print(f"{name}: {n} rows", flush=True)
            except KeyboardInterrupt:
                raise
            except BaseException as exc:  # noqa: BLE001 - one source failing must not stop the rest
                failed = True
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                print(f"{name}: FAILED: {_failure_message(exc)}", flush=True)
    finally:
        conn.close()
    return 1 if failed else 0


def _day(day: date, as_json: bool) -> int:
    if not DB_PATH.exists():
        print('database not found — run "energy sync"')
        return 1
    conn = store.connect(DB_PATH)
    try:
        report = daily.compute_day(conn, day)
    finally:
        conn.close()
    print(json.dumps(report, indent=2) if as_json else daily.format_table(report))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="energy", description="Home energy data integration")
    sub = parser.add_subparsers(dest="command", required=True)
    p_sync = sub.add_parser("sync", help="fetch data from every (or the named) source into SQLite")
    p_sync.add_argument("sources", nargs="*", metavar="SOURCE",
                        help=f"one or more of: {', '.join(SOURCES)} (default: all)")
    p_sync.add_argument("--since", type=_date, help="re-fetch from this date (YYYY-MM-DD)")
    sub.add_parser("ecobee-login", help="interactive ecobee login (prompts for the authenticator code)")
    p_day = sub.add_parser("day", help="print one day's report")
    p_day.add_argument("date", type=_date, help="YYYY-MM-DD")
    p_day.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)

    if args.command == "sync":
        unknown = [s for s in args.sources if s not in SOURCES]
        if unknown:
            p_sync.error(f"unknown source(s): {', '.join(unknown)}; choose from {', '.join(SOURCES)}")
        return _sync(args.sources or list(SOURCES), args.since)
    if args.command == "ecobee-login":
        try:
            ecobee.login_interactive()
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - print a safe one-line error, never a traceback
            print(f"ecobee-login: FAILED: {_failure_message(exc)}")
            return 1
        print("ecobee login saved")
        return 0
    return _day(args.date, args.json)


if __name__ == "__main__":
    sys.exit(main())
