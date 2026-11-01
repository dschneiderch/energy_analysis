"""Emporia Vue hourly kWh per channel via PyEmVue."""
import json
import os
from datetime import date, datetime, time, timedelta, timezone

from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit

from .. import archive, config, store

DEFAULT_START = date(2023, 4, 15)
CHUNK_DAYS = 30
_FMT = "%Y-%m-%dT%H:%M:%SZ"


def rows_from_chart(channel: str, name: str, usage: list, first: datetime, end: datetime) -> list[dict]:
    rows = []
    for i, kwh in enumerate(usage):
        ts = first + timedelta(hours=i)
        if kwh is None or ts >= end:
            continue
        rows.append({"channel": channel, "ts": ts.astimezone(timezone.utc).strftime(_FMT),
                     "kwh": kwh, "channel_name": name})
    return rows


def start_for_channel(conn, channel: str, since: date | None) -> datetime:
    if since:
        day = since
    else:
        latest = conn.execute("SELECT max(ts) FROM emporia_hourly WHERE channel=?", (channel,)).fetchone()[0]
        if not latest:  # channel never returned data: resume from its last successful sync instead
            latest = conn.execute("SELECT max(range_end) FROM sync_log WHERE source=? AND status='ok'",
                                  (f"emporia:{channel}",)).fetchone()[0]
        day = datetime.strptime(latest, _FMT).date() - timedelta(days=2) if latest else DEFAULT_START
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def _login() -> PyEmVue:
    secrets = config.load_secrets()
    config.SECRETS_DIR.mkdir(mode=0o700, exist_ok=True)
    vue = PyEmVue()
    vue.login(username=secrets["EMPORIA_USERNAME"], password=secrets["EMPORIA_PASSWORD"],
              token_storage_file=str(config.SECRETS_DIR / "emporia_tokens.json"))
    tok = config.SECRETS_DIR / "emporia_tokens.json"
    if tok.exists():
        os.chmod(tok, 0o600)
    return vue


def _channels(vue: PyEmVue) -> list[tuple[str, str, object]]:
    seen, out = set(), []
    for dev in vue.get_devices():
        if dev.device_gid != config.EMPORIA_DEVICE_GID:
            continue
        for ch in dev.channels:
            num = str(ch.channel_num)
            if num in seen:
                continue
            seen.add(num)
            name = "mains" if num == config.MAINS_CHANNEL else (ch.name or num)
            out.append((num, name, ch))
    return out


def import_chunks(conn, payload: dict) -> int:
    """Upsert an archived set of get_chart_usage responses."""
    total = 0
    for c in payload["chunks"]:
        start, end = datetime.fromisoformat(c["start"]), datetime.fromisoformat(c["end"])
        first = datetime.fromisoformat(c["first"]) if c["first"] else None
        rows = rows_from_chart(c["channel"], c["channel_name"], c["usage"] or [], first, end) if first else []
        n = store.upsert(conn, "emporia_hourly", rows)
        store.log_sync(conn, f"emporia:{c['channel']}", start.strftime(_FMT), end.strftime(_FMT), n, "ok")
        total += n
    return total


def sync(conn, since: date | None = None) -> int:
    total = archive.import_new(conn, "emporia_file", sorted(config.EMPORIA_DIR.glob("*.json")),
                               lambda p: import_chunks(conn, json.loads(p.read_text())))
    vue = _login()
    end_dt = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    chunks, n = [], 0
    try:
        for num, name, ch in _channels(vue):
            cur = start_for_channel(conn, num, since)
            while cur < end_dt:
                chunk_end = min(cur + timedelta(days=CHUNK_DAYS), end_dt)
                usage, first = vue.get_chart_usage(ch, cur, chunk_end, scale=Scale.HOUR.value,
                                                   unit=Unit.KWH.value)
                chunks.append({"channel": num, "channel_name": name, "start": cur.isoformat(),
                               "end": chunk_end.isoformat(), "first": first.isoformat() if first else None,
                               "usage": usage})
                cur = chunk_end
    finally:
        # Archive before import, and keep what was fetched even if a later call failed:
        # finer-grained Emporia data expires upstream.
        if chunks:
            payload = {"chunks": chunks}
            path = archive.write_json(config.EMPORIA_DIR, f"chart_usage_hour_{end_dt.strftime('%Y-%m-%dT%H')}",
                                      payload)
            n = import_chunks(conn, payload)
            archive.log_imported(conn, "emporia_file", path, n)
    return total + n
