"""Daily runtime summaries from the beestat API (covers pre-2023 thermostat history)."""
import json
from datetime import date

from .. import config, http, store

URL = "https://api.beestat.io/"
USER_AGENT = "energy-analysis/0.1"


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


def sync(conn, since: date | None = None) -> int:
    key = config.load_secrets()["BEESTAT_API_KEY"]
    resp = http.request("GET", URL, params={"api_key": key, "resource": "runtime_thermostat_summary",
                                            "method": "read_id"},
                        headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError("beestat request failed")
    data = body["data"]
    rows = list(data.values()) if isinstance(data, dict) else list(data)
    config.BEESTAT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.BEESTAT_DIR / f"runtime_thermostat_summary_{date.today().isoformat()}.json"
    path.write_text(json.dumps(rows))
    parsed = parse_summaries(rows)
    if since:
        parsed = [r for r in parsed if r["date"] >= since.isoformat()]
    n = store.upsert(conn, "ecobee_daily", parsed)
    store.log_sync(conn, "beestat", since.isoformat() if since else "all", date.today().isoformat(), n, "ok")
    return n
