"""ecobee 5-minute runtime: web login / token refresh, runtimeReport sync, import of the
saved API JSON responses and portal CSV exports, and vacation-event detection."""
import csv
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import archive, config, http, store

# Timestamp basis, determined 2026-10-31 by correlating hourly sums of Living Room
# compHeat1 (+auxHeat1) with hourly Emporia channel 7 + 8 kWh (744 hours each):
#   runtimeReport_2026-01-05_2026-02-04.json: read as UTC r=0.217, read as America/Los_Angeles r=0.849
#   runtimeReport_2025-12-05_2026-01-04.json: read as UTC r=0.363, read as America/Los_Angeles r=0.846
#   (LR + Upstairs heat: UTC 0.206 / local 0.884 and UTC 0.313 / local 0.927)
#   Fixed offsets from the UTC reading peak sharply at +8 h (PST), e.g. +7 h 0.569, +8 h 0.849, +9 h 0.646.
# Rows are thermostat local time. The requested startDate is a UTC day, which is why a
# response starts at the previous local day's 16:00 (PST) / 17:00 (PDT). Fall-back days
# repeat 01:00-01:55 and spring-forward days skip 02:xx, as local wall-clock rows do.
# Sensor rows use the same basis: the Living Room "Thermostat Temperature" sensor (ei:0:1)
# matches zoneAveTemp at zero shift (r=0.9995, mean |diff| 0.002 F) versus r=0.72 at +/-1 h and
# r<0 at +/-7-8 h, and sensor data share the runtime rows' first/last stamps and DST pattern.
# So no separate ECOBEE_SENSOR_TS_IS_UTC is needed.
ECOBEE_TS_IS_UTC = False

TOKEN_URL = "https://auth.ecobee.com/oauth/token"
CLIENT_ID = "183eORFPlXyz9BbDZwqexHPBQoVjgadh"
REPORT_URL = "https://api.ecobee.com/1/runtimeReport"
REQUEST_COLUMNS = ("auxHeat1,compCool1,compHeat1,fan,outdoorTemp,zoneAveTemp,zoneHeatTemp,zoneCoolTemp,"
                   "hvacMode,zoneHvacMode,zoneCalendarEvent,zoneClimate")
CHUNK_DAYS = 31
DEFAULT_LOOKBACK_DAYS = 600

_FMT = "%Y-%m-%dT%H:%M:%SZ"
_TZ = ZoneInfo(config.LOCAL_TZ)

# API column (lower-cased: the API spells some as HVACmode / zoneHVACmode) -> ecobee_interval column
_API_MAP = {
    "compheat1": "comp_heat_s", "compcool1": "comp_cool_s", "auxheat1": "aux_heat_s", "fan": "fan_s",
    "outdoortemp": "outdoor_temp_f", "zoneavetemp": "indoor_temp_f",
    "zoneheattemp": "heat_setpoint_f", "zonecooltemp": "cool_setpoint_f",
    "hvacmode": "hvac_mode", "zonehvacmode": "zone_hvac_mode",
    "zonecalendarevent": "calendar_event", "zoneclimate": "climate",
}
_TEXT_COLS = {"hvac_mode", "zone_hvac_mode", "calendar_event", "climate"}

_VACATION_RES = (re.compile(r"^\d{9,}$"), re.compile(r"^[0-9A-F]{8,}$"), re.compile(r"^Vacation"))


class EcobeeLoginRequired(RuntimeError):
    pass


def _token_path() -> Path:
    return config.SECRETS_DIR / "ecobee_tokens.json"


def _save_tokens(tokens: dict) -> None:
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(tokens, f)
    os.chmod(path, 0o600)


def to_utc(d: str, t: str, fold: int) -> str:
    naive = datetime.fromisoformat(f"{d}T{t}")
    if ECOBEE_TS_IS_UTC:
        aware = naive.replace(tzinfo=timezone.utc)
    else:
        aware = naive.replace(tzinfo=_TZ, fold=fold)
    return aware.astimezone(timezone.utc).strftime(_FMT)


class _Folder:
    """Assigns fold=1 to the second occurrence of a local (date, time) in one row sequence."""

    def __init__(self):
        self.seen: set[tuple[str, str]] = set()

    def ts(self, d: str, t: str) -> str:
        key = (d, t)
        fold = 1 if key in self.seen else 0
        self.seen.add(key)
        return to_utc(d, t, fold)


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    v = v.strip()
    if v == "":
        return None
    f = float(v)
    return int(f) if f.is_integer() else f


def _text(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def parse_runtime_report(payload: dict) -> tuple[list[dict], list[dict]]:
    cols = [c.strip().lower() for c in payload["columns"].split(",")]
    intervals = []
    for report in payload.get("reportList", []):
        tid = report["thermostatIdentifier"]
        folder = _Folder()
        for line in report["rowList"]:
            parts = line.split(",")
            d, t = parts[0], parts[1]
            ts = folder.ts(d, t)
            values = dict(zip(cols, parts[2:]))
            if not (values.get("hvacmode") or "").strip() and not (values.get("zonehvacmode") or "").strip():
                continue  # pre-retention filler row
            row = {"thermostat_id": tid, "ts": ts}
            for api_col, col in _API_MAP.items():
                raw = values.get(api_col)
                row[col] = _text(raw) if col in _TEXT_COLS else _num(raw)
            row["source"] = "api"
            intervals.append(row)
    sensors = []
    for block in payload.get("sensorList", []):
        tid = block["thermostatIdentifier"]
        meta = {s["sensorId"]: (s["sensorName"].strip(), s["sensorType"]) for s in block.get("sensors", [])}
        columns = block["columns"]
        folder = _Folder()
        for line in block.get("data", []):
            parts = line.split(",")
            ts = folder.ts(parts[0], parts[1])
            for sid, raw in zip(columns[2:], parts[2:]):
                value = _num(raw)
                if value is None or sid not in meta:
                    continue
                name, kind = meta[sid]
                sensors.append({"thermostat_id": tid, "ts": ts, "sensor_name": name, "kind": kind, "value": value})
    return intervals, sensors


def _portal_sensor_columns(fields: list[str]) -> list[tuple[str, str, str]]:
    """(column, sensor name, kind) for the remote-sensor columns after "DM Offset": "<name> (F)",
    "<name> (%RH)" and "<name>2" (occupancy). Names match the API's sensorName once stripped."""
    after = fields[fields.index("DM Offset") + 1:] if "DM Offset" in fields else []
    temps = {c[:-4] for c in after if c.endswith(" (F)")}
    out = []
    for c in after:
        if c.endswith(" (F)"):
            out.append((c, c[:-4].strip(), "temperature"))
        elif c.endswith(" (%RH)"):
            out.append((c, c[:-6].strip(), "humidity"))
        elif c[:-1] in temps:
            out.append((c, c[:-1].strip(), "occupancy"))
    return out


def parse_portal_csv(path: Path) -> tuple[list[dict], list[dict]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        head = [next(f) for _ in range(5)]
        tid = next(line.strip().split(",")[3] for line in head if line.startswith("#,Thermostat,identifier,"))
        reader = csv.DictReader(f)
        sensor_cols = _portal_sensor_columns(reader.fieldnames or [])
        upstairs = tid == config.UP_ECOBEE_ID
        folder = _Folder()
        out, sensors = [], []
        for r in reader:
            r.pop(None, None)  # trailing empty field
            d, t = r["Date"], r["Time"]
            ts = folder.ts(d, t)
            for col, name, kind in sensor_cols:
                value = _num(r.get(col))
                if value is not None:
                    sensors.append({"thermostat_id": tid, "ts": ts, "sensor_name": name, "kind": kind, "value": value})
            if not (r.get("System Mode") or "").strip():
                continue
            heat1 = _num(r.get("Heat Stage 1 (sec)"))
            if upstairs:
                comp_heat, aux_heat = 0, heat1
            else:
                comp_heat, aux_heat = heat1, _num(r.get("Aux Heat 1 (sec)"))
            out.append({
                "thermostat_id": tid, "ts": ts,
                "comp_heat_s": comp_heat, "comp_cool_s": _num(r.get("Cool Stage 1 (sec)")),
                "aux_heat_s": aux_heat, "fan_s": _num(r.get("Fan (sec)")),
                "outdoor_temp_f": _num(r.get("Outdoor Temp (F)")),
                "indoor_temp_f": _num(r.get("Current Temp (F)")),
                "heat_setpoint_f": _num(r.get("Heat Set Temp (F)")),
                "cool_setpoint_f": _num(r.get("Cool Set Temp (F)")),
                "hvac_mode": _text(r.get("System Setting")),
                "zone_hvac_mode": _text(r.get("System Mode")),
                "calendar_event": _text(r.get("Calendar Event")),
                "climate": _text(r.get("Program Mode")),
                "source": "portal_csv",
            })
    return out, sensors


def import_portal_csv(conn, path: Path) -> int:
    """Upsert a portal CSV and its sensor readings, skipping intervals that already have an API row
    (the API row wins)."""
    rows, sensors = parse_portal_csv(path)
    if not rows and not sensors:
        return 0
    stamps = [r["ts"] for r in rows + sensors]
    have_api = {(r[0], r[1]) for r in conn.execute(
        "SELECT thermostat_id, ts FROM ecobee_interval WHERE source = 'api' AND ts BETWEEN ? AND ?",
        (min(stamps), max(stamps)))}
    store.upsert(conn, "ecobee_sensor", [r for r in sensors if (r["thermostat_id"], r["ts"]) not in have_api])
    return store.upsert(conn, "ecobee_interval",
                        [r for r in rows if (r["thermostat_id"], r["ts"]) not in have_api])


def import_payload(conn, payload: dict) -> tuple[int, int]:
    intervals, sensors = parse_runtime_report(payload)
    return store.upsert(conn, "ecobee_interval", intervals), store.upsert(conn, "ecobee_sensor", sensors)


def _vacation_name(event: str | None) -> str | None:
    if not event:
        return None
    name = event.replace("(SmartRecovery)", "").strip()
    return name if any(rx.match(name) for rx in _VACATION_RES) else None


def derive_vacations(conn) -> int:
    days: dict[str, set[date]] = defaultdict(set)
    for ts, event in conn.execute(
            "SELECT ts, calendar_event FROM ecobee_interval WHERE calendar_event IS NOT NULL"):
        name = _vacation_name(event)
        if name is None:
            continue
        local = datetime.strptime(ts, _FMT).replace(tzinfo=timezone.utc).astimezone(_TZ)
        days[name].add(local.date())
    rows = []
    for name, ds in days.items():
        # One range per run of consecutive dates, so a reused name ("Vacation") on two
        # separate trips does not become one long absence.
        ordered = sorted(ds)
        start = prev = ordered[0]
        for d in ordered[1:]:
            if d - prev > timedelta(days=1):
                rows.append((start, prev))
                start = d
            prev = d
        rows.append((start, prev))
    existing = {(r[0], r[1]): r[2] for r in conn.execute("SELECT start_date, end_date, source FROM not_home")}
    out = []
    for s, e in sorted(set(rows)):
        key = (s.isoformat(), e.isoformat())
        if existing.get(key, "ecobee_vacation") != "ecobee_vacation":
            continue  # same range already recorded from exclude_dates.txt; keep that source
        out.append({"start_date": key[0], "end_date": key[1], "source": "ecobee_vacation"})
    return store.upsert(conn, "not_home", out)


def login_interactive() -> None:
    from pyecobee import Ecobee
    from pyecobee.errors import EcobeeAuthMfaRequiredError

    secrets = config.load_secrets()
    eco = Ecobee(config={"API_KEY": None})
    eco.username = secrets["ECOBEE_USERNAME"]
    eco.password = secrets["ECOBEE_PASSWORD"]
    try:
        eco.request_tokens_web()
    except EcobeeAuthMfaRequiredError as err:
        code = input("ecobee authenticator code: ").strip()
        eco.submit_mfa_code(err.challenge, code)
    _save_tokens({"access_token": eco.access_token, "refresh_token": eco.refresh_token})


def access_token() -> str:
    path = _token_path()
    if not path.exists():
        raise EcobeeLoginRequired("run `energy ecobee-login`")
    try:
        tokens = json.loads(path.read_text())
        refresh = tokens.get("refresh_token")
    except (ValueError, AttributeError):
        raise EcobeeLoginRequired("token file unreadable; run `energy ecobee-login`") from None
    if not refresh:
        raise EcobeeLoginRequired("run `energy ecobee-login`")
    resp = http.request("POST", TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": refresh,
                                                 "client_id": CLIENT_ID})
    if 400 <= resp.status_code < 500:
        raise EcobeeLoginRequired("run `energy ecobee-login`")
    http.raise_for_status(resp)
    body = resp.json()
    token = body.get("access_token")
    if not token:
        raise EcobeeLoginRequired("run `energy ecobee-login`")
    _save_tokens({"access_token": token, "refresh_token": body.get("refresh_token") or refresh})
    return token


def _fetch(token: str, start: date, end: date) -> dict:
    body = {"selection": {"selectionType": "thermostats",
                          "selectionMatch": f"{config.LR_ECOBEE_ID},{config.UP_ECOBEE_ID}"},
            "startDate": start.isoformat(), "endDate": end.isoformat(),
            "columns": REQUEST_COLUMNS, "includeSensors": True}
    resp = http.request("GET", REPORT_URL, params={"format": "json", "body": json.dumps(body)},
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    http.raise_for_status(resp)
    payload = resp.json()
    status = payload.get("status") or {}
    if status.get("code", 0) != 0:
        raise RuntimeError(f"ecobee runtimeReport status {status.get('code')}: {status.get('message', '')}")
    return payload


def _api_start(conn, since: date | None, today: date) -> date:
    if since:
        return since
    latest = [conn.execute("SELECT max(ts) FROM ecobee_interval WHERE source = 'api' AND thermostat_id = ?",
                           (tid,)).fetchone()[0] for tid in (config.LR_ECOBEE_ID, config.UP_ECOBEE_ID)]
    if None in latest:
        return today - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    # Resume from the older thermostat's newest row, so a backlog it uploads after an outage is fetched.
    return date.fromisoformat(min(latest)[:10]) - timedelta(days=2)


def sync(conn, since: date | None = None) -> int:
    # 1 + 2: archived files (re-importing is harmless but the sensor rows make it slow).
    total = archive.import_new(conn, "ecobee_file", sorted(config.ECOBEE_PORTAL_DIR.glob("*.csv")),
                               lambda p: import_portal_csv(conn, p))
    total += archive.import_new(conn, "ecobee_file", sorted(config.ECOBEE_API_DIR.glob("*.json")),
                                lambda p: import_payload(conn, json.loads(p.read_text()))[0])

    try:
        # 3: live API. startDate/endDate are UTC days, so end on the UTC date to reach the
        # current local evening. Once a window ages out of ecobee's retention, a re-fetch returns
        # filler rows, and the archived file is the only copy.
        today = datetime.now(timezone.utc).date()
        token = access_token()
        cur = _api_start(conn, since, today)
        while cur <= today:
            end = min(cur + timedelta(days=CHUNK_DAYS - 1), today)
            payload = _fetch(token, cur, end)
            path = archive.write_json(config.ECOBEE_API_DIR, f"runtimeReport_{cur}_{end}", payload)
            n, _ = import_payload(conn, payload)
            archive.log_imported(conn, "ecobee_file", path, n)
            store.log_sync(conn, "ecobee", cur.isoformat(), end.isoformat(), n, "ok")
            total += n
            cur = end + timedelta(days=1)
    finally:
        # 4: vacations, derived from stored intervals even if the API step fails
        derive_vacations(conn)
    return total
