"""Emporia Vue hourly kWh per channel via PyEmVue."""
import os
from datetime import date, datetime, time, timedelta, timezone

from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit

from .. import config, store

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


def sync(conn, since: date | None = None) -> int:
    vue = _login()
    channels = _channels(vue)
    end_dt = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    total = 0
    for num, name, ch in channels:
        cur = start_for_channel(conn, num, since)
        while cur < end_dt:
            chunk_end = min(cur + timedelta(days=CHUNK_DAYS), end_dt)
            usage, first = vue.get_chart_usage(ch, cur, chunk_end, scale=Scale.HOUR.value,
                                               unit=Unit.KWH.value)
            rows = rows_from_chart(num, name, usage or [], first, chunk_end) if first else []
            n = store.upsert(conn, "emporia_hourly", rows)
            store.log_sync(conn, f"emporia:{num}", cur.strftime(_FMT), chunk_end.strftime(_FMT), n, "ok")
            total += n
            cur = chunk_end
    return total
