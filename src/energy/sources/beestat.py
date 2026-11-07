"""Daily runtime summaries from the beestat API (covers pre-2023 thermostat history).
API docs: https://beestat.notion.site/API-Documentation-e4a7746e6a3f45dbb58ea6b45b8f9744"""
import json
from datetime import date, timedelta

from .. import archive, config, http, store

URL = "https://api.beestat.io/"
USER_AGENT = "energy-analysis/0.1"
# Only these get a runtime.sync; the retired 47722 makes it hang for ~2 minutes and return false.
LIVE_THERMOSTATS = (config.BEESTAT_LR, config.BEESTAT_UP)
# beestat's own copy "can get up to a week behind" (API docs), so a week of stored days can still change.
LOOKBACK_DAYS = 7


def parse_summaries(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if r.get("deleted"):
            continue
        out.append({
            "thermostat_id": r["thermostat_id"], "date": r["date"],
            "comp_heat_s": r.get("sum_compressor_heat_1"),
            "comp_cool_s": r.get("sum_compressor_cool_1"),
            "aux_heat_s": r.get("sum_auxiliary_heat_1"),
            "fan_s": r.get("sum_fan"),
            "avg_outdoor_f": r.get("avg_outdoor_temperature"),
            "min_outdoor_f": r.get("min_outdoor_temperature"),
            "max_outdoor_f": r.get("max_outdoor_temperature"),
            "avg_indoor_f": r.get("avg_indoor_temperature"),
            "interval_count": r.get("count"),
            "source": "beestat",
        })
    return out


def _rows(body) -> list[dict]:
    data = body["data"] if isinstance(body, dict) else body  # files before 2026-11-01 hold only the rows
    return list(data.values()) if isinstance(data, dict) else list(data)


def import_summaries(conn, body) -> int:
    """Upsert an archived runtime_thermostat_summary response."""
    return store.upsert(conn, "ecobee_daily", parse_summaries(_rows(body)))


def import_profiles(conn, body) -> int:
    """Upsert an archived profile response (beestat's weekly temperature profiles)."""
    return store.upsert(conn, "beestat_profile", [
        {"profile_id": r["profile_id"], "thermostat_id": r["thermostat_id"], "date": r["date"],
         "profile": json.dumps(r["profile"])} for r in _rows(body) if not r.get("deleted")])


def _call(key: str, resource: str, method: str, arguments: dict | None = None) -> dict:
    params = {"api_key": key, "resource": resource, "method": method}
    if arguments is not None:
        params["arguments"] = json.dumps(arguments)
    resp = http.request("GET", URL, params=params, headers={"User-Agent": USER_AGENT})
    http.raise_for_status(resp)
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(f"beestat {resource}.{method} failed: {(body.get('data') or {}).get('error_message')}")
    return body


def _read_start(conn, since: date | None) -> date | None:
    """First date to read: `since`, else a week before the older live thermostat's newest day,
    else None (read everything)."""
    if since:
        return since
    latest = [conn.execute("SELECT max(date) FROM ecobee_daily WHERE thermostat_id=?", (str(t),)).fetchone()[0]
              for t in LIVE_THERMOSTATS]
    return None if None in latest else date.fromisoformat(min(latest)) - timedelta(days=LOOKBACK_DAYS)


def sync(conn, since: date | None = None) -> int:
    # Files sort oldest first, so the newest archived row wins.
    total = archive.import_new(conn, "beestat_file", sorted(config.BEESTAT_DIR.glob("runtime_thermostat_summary_*.json")),
                               lambda p: import_summaries(conn, json.loads(p.read_text())))
    total += archive.import_new(conn, "beestat_profile_file", sorted(config.BEESTAT_DIR.glob("profile_*.json")),
                                lambda p: import_profiles(conn, json.loads(p.read_text())))
    key = config.load_secrets()["BEESTAT_API_KEY"]
    for tid in LIVE_THERMOSTATS:
        _call(key, "runtime", "sync", {"thermostat_id": tid})
    start = _read_start(conn, since)
    body = _call(key, "runtime_thermostat_summary", "read_id",
                 {"attributes": {"date": {"value": start.isoformat(), "operator": ">="}}} if start else None)
    # Archive the raw response before parsing: the beestat API is provisional and may change.
    path = archive.write_json(config.BEESTAT_DIR, f"runtime_thermostat_summary_{date.today()}", body)
    n = import_summaries(conn, body)
    archive.log_imported(conn, "beestat_file", path, n)
    store.log_sync(conn, "beestat", start.isoformat() if start else "all", date.today().isoformat(), n, "ok")

    # profile.read_id is public but missing from the API docs. beestat regenerates the current week's
    # profile in place (same profile_id, later date), so re-read from the newest stored date.
    latest = conn.execute("SELECT max(date) FROM beestat_profile").fetchone()[0]
    body = _call(key, "profile", "read_id",
                 {"attributes": {"date": {"value": latest, "operator": ">="}}} if latest else None)
    path = archive.write_json(config.BEESTAT_DIR, f"profile_{date.today()}", body)
    n_profiles = import_profiles(conn, body)
    archive.log_imported(conn, "beestat_profile_file", path, n_profiles)
    return total + n + n_profiles
