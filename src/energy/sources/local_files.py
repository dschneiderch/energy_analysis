"""Load committed/local reference files: rates, not-home dates, gas usage."""
import csv
import re
from datetime import date, datetime

from energy import config, store


def _d(s: str) -> date:
    y, m, d = s.strip().split("-")
    return date(int(y), int(m), int(d))


def parse_exclude_dates(text: str) -> list[tuple[date, date]]:
    out = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            parts = re.split(r"\s+--\s+", line)
            if len(parts) > 2:
                raise ValueError("too many parts")
            start = _d(parts[0])
            end = _d(parts[1]) if len(parts) > 1 else start
        except ValueError as exc:
            raise ValueError(f"exclude_dates line {n}: cannot parse {raw.strip()!r}") from exc
        if end < start:
            raise ValueError(f"exclude_dates line {n}: end before start in {raw.strip()!r}")
        out.append((start, end))
    return out


def _num(s: str | None) -> float | None:
    s = (s or "").strip()
    return float(s) if s else None


def _load_rates(conn) -> int:
    path = config.RATES_CSV
    if not path.exists():
        return 0
    with open(path, newline="", encoding="utf-8") as fh:
        rows = [{
            "fuel": r["fuel"], "schedule": r["schedule"], "effective_date": r["effective_date"],
            "component": r["component"], "tier_min": float(r["tier_min"]),
            "tier_max": _num(r["tier_max"]), "rate_per_unit": _num(r["rate_per_unit"]),
            "unit": r["unit"],
        } for r in csv.DictReader(fh)]
    by_sched: dict[tuple, set] = {}
    for r in rows:
        by_sched.setdefault((r["fuel"], r["schedule"]), {}).setdefault(r["effective_date"], set()).add(r["component"])
    for (fuel, sched), dates in by_sched.items():
        for d, comps in sorted(dates.items()):
            if "energy" not in comps:
                raise ValueError(f"rates.csv: effective_date {d} ({fuel} schedule {sched}) has no 'energy' component")
    for r in rows:
        if r["component"] == "energy" and r["rate_per_unit"] is None:
            raise ValueError(f"rates.csv: effective_date {r['effective_date']} ({r['fuel']} schedule "
                             f"{r['schedule']}) has an energy tier with no rate")
    cols = list(rows[0]) if rows else []
    with conn:  # single transaction: replace the table with the file's contents
        conn.execute("DELETE FROM rates")
        for r in rows:
            conn.execute(f"INSERT OR REPLACE INTO rates ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         [r[c] for c in cols])
    return len(rows)


def _load_not_home(conn) -> int:
    path = config.EXCLUDE_DATES_FILE
    ranges = parse_exclude_dates(path.read_text(encoding="utf-8")) if path.exists() else []
    with conn:  # single transaction
        conn.execute("DELETE FROM not_home WHERE source='exclude_dates'")
        for s, e in ranges:
            conn.execute("INSERT OR REPLACE INTO not_home (start_date, end_date, source) VALUES (?,?,?)",
                         (s.isoformat(), e.isoformat(), "exclude_dates"))
    return len(ranges)


def _load_gas(conn) -> int:
    gas_dir = config.GAS_DIR
    if not gas_dir.exists():
        return 0
    # Keyed by read time, so overlapping downloads count once; a read two files disagree on fails the sync.
    reads: dict[datetime, tuple[float, str]] = {}
    files = sorted(gas_dir.glob("avista-gas-daily-*.csv"))
    if not files:
        raise ValueError(f"no avista-gas-daily-*.csv files in {gas_dir}; gas table left unchanged")
    for f in files:
        with open(f, newline="", encoding="utf-8-sig") as fh:
            started = False
            for row in csv.reader(fh):
                if not started:
                    started = bool(row) and row[0].strip() == "Date/Time"
                    continue
                if len(row) < 2 or not row[0].strip():
                    continue
                v = _num(row[1])
                if v is None:
                    continue
                ts = datetime.strptime(row[0].strip(), "%Y-%m-%d %H:%M:%S")
                if ts in reads and reads[ts][0] != v:
                    raise ValueError(f"gas files disagree on the {ts} read: {reads[ts][1]} has {reads[ts][0]}, "
                                     f"{f.name} has {v}")
                reads[ts] = (v, f.name)
    ccf: dict[date, float] = {}
    for ts, (v, _) in reads.items():
        ccf[ts.date()] = ccf.get(ts.date(), 0.0) + v
    with conn:  # single transaction: replace the table with the files' contents
        conn.execute("DELETE FROM gas_daily")
        return store.upsert(conn, "gas_daily", [{"date": d.isoformat(), "ccf": v} for d, v in sorted(ccf.items())])


def sync(conn, since=None) -> int:
    """Reload all local reference files (always in full; `since` is accepted and ignored)."""
    rates, not_home, gas = _load_rates(conn), _load_not_home(conn), _load_gas(conn)
    print(f"  {config.RATES_CSV.name}: {rates} rates; {config.EXCLUDE_DATES_FILE.name}: {not_home} ranges; "
          f"gas: {gas} days", flush=True)
    return rates + not_home + gas
