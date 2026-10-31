"""Grid carbon intensity from Home Assistant long-term statistics (websocket API)."""
import asyncio
import json
from datetime import date, datetime, time, timedelta, timezone

import websockets

from .. import config, store

URL = "ws://192.168.50.206:8123/api/websocket"
CO2_ID = "sensor.electricity_maps_co2_intensity"
FOSSIL_ID = "sensor.electricity_maps_grid_fossil_fuel_percentage"
DEFAULT_START = date(2024, 2, 1)
CHUNK_DAYS = 90
_FMT = "%Y-%m-%dT%H:%M:%SZ"


def parse_statistics(result: dict) -> list[dict]:
    by_ts: dict[str, dict] = {}
    for sid, col in ((CO2_ID, "co2_g_per_kwh"), (FOSSIL_ID, "fossil_pct")):
        for item in result.get(sid) or []:
            ts = datetime.fromtimestamp(item["start"] / 1000, tz=timezone.utc).strftime(_FMT)
            row = by_ts.setdefault(ts, {"ts": ts, "co2_g_per_kwh": None, "fossil_pct": None})
            row[col] = item.get("mean")
    return [by_ts[k] for k in sorted(by_ts)]


async def _fetch(token: str, windows: list[tuple[datetime, datetime]]) -> list[dict]:
    results = []
    async with websockets.connect(URL, max_size=None) as ws:
        await ws.recv()  # auth_required
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        msg = json.loads(await ws.recv())
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"Home Assistant auth failed: {msg.get('type')}")
        for i, (a, b) in enumerate(windows, start=1):
            await ws.send(json.dumps({
                "id": i, "type": "recorder/statistics_during_period",
                "start_time": a.isoformat(), "end_time": b.isoformat(),
                "statistic_ids": [CO2_ID, FOSSIL_ID], "period": "hour", "types": ["mean"]}))
            while True:
                reply = json.loads(await ws.recv())
                if reply.get("id") != i:
                    continue
                if not reply.get("success"):
                    raise RuntimeError(f"Home Assistant request failed: {reply.get('error')}")
                results.append(reply["result"])
                break
    return results


def sync(conn, since: date | None = None) -> int:
    if since:
        start_day = since
    else:
        latest = conn.execute("SELECT max(ts) FROM grid_carbon_hourly").fetchone()[0]
        start_day = (datetime.strptime(latest, _FMT).date() - timedelta(days=2)) if latest else DEFAULT_START
    end = datetime.now(timezone.utc)
    windows, cur = [], datetime.combine(start_day, time.min, tzinfo=timezone.utc)
    while cur < end:
        nxt = min(cur + timedelta(days=CHUNK_DAYS), end)
        windows.append((cur, nxt))
        cur = nxt
    token = config.load_secrets()["HA_TOKEN"]
    results = asyncio.run(_fetch(token, windows))
    total = 0
    for (a, b), res in zip(windows, results):
        n = store.upsert(conn, "grid_carbon_hourly", parse_statistics(res))
        store.log_sync(conn, "ha_carbon", a.strftime(_FMT), b.strftime(_FMT), n, "ok")
        total += n
    return total
