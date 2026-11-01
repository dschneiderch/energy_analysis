"""SQLite store: schema, upserts, sync log, local-day helpers."""
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DB_PATH, LOCAL_TZ

SCHEMA = """
CREATE TABLE IF NOT EXISTS ecobee_interval (
    thermostat_id TEXT NOT NULL, ts TEXT NOT NULL,
    comp_heat_s REAL, comp_cool_s REAL, aux_heat_s REAL, fan_s REAL,
    outdoor_temp_f REAL, indoor_temp_f REAL, heat_setpoint_f REAL, cool_setpoint_f REAL,
    hvac_mode TEXT, zone_hvac_mode TEXT, calendar_event TEXT, climate TEXT, source TEXT,
    PRIMARY KEY (thermostat_id, ts)
);
CREATE TABLE IF NOT EXISTS ecobee_sensor (
    thermostat_id TEXT NOT NULL, ts TEXT NOT NULL,
    sensor_name TEXT NOT NULL, kind TEXT NOT NULL, value REAL,
    PRIMARY KEY (thermostat_id, ts, sensor_name, kind)
);
CREATE TABLE IF NOT EXISTS ecobee_daily (
    thermostat_id TEXT NOT NULL, date TEXT NOT NULL,
    comp_heat_s REAL, comp_cool_s REAL, aux_heat_s REAL, fan_s REAL,
    avg_outdoor_f REAL, min_outdoor_f REAL, max_outdoor_f REAL, avg_indoor_f REAL,
    interval_count INTEGER, source TEXT,
    PRIMARY KEY (thermostat_id, date)
);
CREATE TABLE IF NOT EXISTS emporia_hourly (
    channel TEXT NOT NULL, ts TEXT NOT NULL, kwh REAL, channel_name TEXT,
    PRIMARY KEY (channel, ts)
);
CREATE INDEX IF NOT EXISTS emporia_hourly_ts ON emporia_hourly (ts);
CREATE TABLE IF NOT EXISTS grid_carbon_hourly (
    ts TEXT NOT NULL PRIMARY KEY, co2_g_per_kwh REAL, fossil_pct REAL
);
CREATE TABLE IF NOT EXISTS weather_hourly (
    ts TEXT NOT NULL PRIMARY KEY, temp_f REAL, wind_mph REAL, source TEXT
);
CREATE TABLE IF NOT EXISTS gas_daily (
    date TEXT NOT NULL PRIMARY KEY, therms REAL, ccf REAL
);
CREATE TABLE IF NOT EXISTS rates (
    fuel TEXT NOT NULL, schedule TEXT NOT NULL, effective_date TEXT NOT NULL,
    component TEXT NOT NULL, tier_min REAL NOT NULL, tier_max REAL,
    rate_per_unit REAL, unit TEXT,
    PRIMARY KEY (fuel, schedule, effective_date, component, tier_min)
);
CREATE TABLE IF NOT EXISTS not_home (
    start_date TEXT NOT NULL, end_date TEXT NOT NULL, source TEXT,
    PRIMARY KEY (start_date, end_date)
);
CREATE TABLE IF NOT EXISTS sync_log (
    source TEXT NOT NULL, range_start TEXT NOT NULL, range_end TEXT NOT NULL,
    fetched_at TEXT, rows INTEGER, status TEXT,
    PRIMARY KEY (source, range_start, range_end)
);
"""

_UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"


def connect(path: str | Path = DB_PATH) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def upsert(conn: sqlite3.Connection, table: str, rows: Iterable[dict]) -> int:
    if table not in _table_names(conn):
        raise ValueError(f"unknown table: {table}")
    count = 0
    for row in rows:
        cols = list(row)
        sql = (f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
               f"VALUES ({', '.join('?' * len(cols))})")
        conn.execute(sql, [row[c] for c in cols])
        count += 1
    conn.commit()
    return count


def log_sync(conn: sqlite3.Connection, source: str, range_start: str, range_end: str,
             rows: int, status: str) -> None:
    fetched_at = datetime.now(timezone.utc).strftime(_UTC_FMT)
    conn.execute(
        "INSERT OR REPLACE INTO sync_log (source, range_start, range_end, fetched_at, rows, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source, range_start, range_end, fetched_at, rows, status),
    )
    conn.commit()


def local_day_bounds(day: date) -> tuple[str, str]:
    """UTC ISO start (inclusive) and end (exclusive) of the local day."""
    tz = ZoneInfo(LOCAL_TZ)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return (start.astimezone(timezone.utc).strftime(_UTC_FMT),
            end.astimezone(timezone.utc).strftime(_UTC_FMT))


def local_day_hours(day: date) -> list[str]:
    """UTC hour-start ISO strings covering the local day (23, 24 or 25 of them)."""
    start, end = (datetime.strptime(s, _UTC_FMT).replace(tzinfo=timezone.utc) for s in local_day_bounds(day))
    out = []
    while start < end:
        out.append(start.strftime(_UTC_FMT))
        start += timedelta(hours=1)
    return out
