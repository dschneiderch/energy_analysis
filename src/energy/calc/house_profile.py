"""Profiles with beestat's sampling rules but corrected inputs (docs/research/house-profile-options.md).
Unlike profile.py, a faithful beestat port kept for comparison: each thermostat's runtime is mapped
by its own heat type, and the indoor temperature can be a fixed average of chosen room sensors."""
from collections import defaultdict
from datetime import date, datetime, timezone
from statistics import mean

from energy import config
from energy.calc import profile as prof

# The Upstairs thermostat's own sensor sits where it tracks the Living Room heating zone, so it is left out.
UPSTAIRS_ROOMS = ("Bedroom", "Kids' Room", "Office Guest Room")


def load_rows(conn, begin: int, end: int, rooms: dict[str, tuple[str, ...]] | None = None) -> dict[int, dict[str, dict]]:
    by_type = {ht: prof.load_rows(conn, begin, end, ht) for ht in set(prof.HEAT_TYPE.values())}
    any_rows = next(iter(by_type.values()))
    rows = {t: {tid: by_type[prof.HEAT_TYPE[tid]][t][tid] for tid in at} for t, at in any_rows.items()}
    start = datetime.fromtimestamp(begin - prof.LOOK, timezone.utc).strftime(prof._FMT)
    stop = datetime.fromtimestamp(end + prof.LOOK, timezone.utc).strftime(prof._FMT)
    for tid, names in (rooms or {}).items():
        temps: dict[int, dict[str, float]] = defaultdict(dict)
        for ts, name, value in conn.execute(
                "SELECT ts, sensor_name, value FROM ecobee_sensor WHERE thermostat_id=? AND kind='temperature' "
                "AND ts >= ? AND ts < ?", (tid, start, stop)):
            if name in names:
                temps[int(datetime.strptime(ts, prof._FMT).replace(tzinfo=timezone.utc).timestamp())][name] = value
        for t, at in rows.items():
            if tid in at:
                s = temps.get(t, {})
                at[tid]["indoor"] = mean(s.values()) if len(s) == len(names) else None
                # A fixed set of rooms doesn't jump when the comfort setting changes, so no 30-minute trims.
                at[tid]["event"] = at[tid]["climate"] = None
    return rows


# Resist minimum for the Upstairs 3-room average (beestat: 30). Tested 2026-11-07: the average's medians move
# <= 0.02 °F/h from 30 to 15 min with about 2x the samples, but single rooms and zoneAveTemp shift by up to
# 0.2 °F/h (post-cycle coasting near vents), so they keep 30 (docs/research/house-profile-options.md).
ROOM_AVERAGE_MIN_RESIST_MINUTES = 15
# Heat pump (channels 7 + 8) standby is ~0.034 kWh/h; hours with both thermostats off stay below 0.071 in 99 %,
# hours with >= 5 min of runtime use >= 0.105 in 99 % (2025-04 .. 2026-11).
HP_OFF_KWH = 0.08


def gate_with_emporia(conn, rows: dict[int, dict[str, dict]], tid: str, begin: int, end: int) -> None:
    """Replace the other thermostats' rows with a stand-in that is "on" in every hour the heat pump drew
    power (or has no Emporia reading), for periods without their 5-minute data. Only whole hours with
    the compressor off can then yield resist samples; heat samples for `tid` disappear."""
    start = datetime.fromtimestamp(begin - prof.LOOK, timezone.utc).strftime(prof._FMT)
    stop = datetime.fromtimestamp(end + prof.LOOK, timezone.utc).strftime(prof._FMT)
    channels = config.HP_CHANNELS
    hours = {int(datetime.strptime(ts, prof._FMT).replace(tzinfo=timezone.utc).timestamp()): kwh
             for ts, kwh in conn.execute(
                 f"SELECT ts, sum(kwh) FROM emporia_hourly WHERE channel IN ({','.join('?' * len(channels))}) "
                 "AND ts >= ? AND ts < ? GROUP BY ts HAVING count(*) = ?", (*channels, start, stop, len(channels)))}
    for t, at in rows.items():
        if tid not in at:
            continue
        kwh = hours.get(t // 3600 * 3600)
        on = 300 if kwh is None or kwh >= HP_OFF_KWH else 0
        stand_in = {"ts": t, "indoor": 0.0, "outdoor": at[tid]["outdoor"], "heat_1": on, "auxiliary_heat_1": 0,
                    "cool_1": 0, "event": None, "climate": None}
        for other in prof.GROUP:
            if other != tid:
                at[other] = stand_in


def heating_season(start: date, end: date) -> list[tuple[date, date]]:
    """The October–May parts of `start`..`end` (both included); monthly runtime shows no heating outside them."""
    spans = []
    for year in range(start.year - 1, end.year + 1):
        a, b = max(start, date(year, 10, 1)), min(end, date(year + 1, 5, 31))
        if a <= b:
            spans.append((a, b))
    return spans


def generate(conn, tid: str, spans: list[tuple[date | datetime, date | datetime]],
             rooms: tuple[str, ...] | None = None, emporia_gate: bool = False,
             min_resist_minutes: int = prof.MIN_DURATION["resist"] // 60) -> dict:
    """Profile for thermostat `tid` over one or more (start, end) spans, pooled into one set of bins;
    `rooms` replaces its indoor temperature with those sensors' mean; `emporia_gate` decides "all off"
    from hourly heat pump kWh instead of the other thermostats (see gate_with_emporia)."""
    min_duration = {**prof.MIN_DURATION, "resist": min_resist_minutes * 60}
    binned: dict[str, dict[int, list[float]]] = {k: defaultdict(list) for k in prof.TYPES}
    for start, end in spans:
        begin, stop = prof.window(start, end)
        rows = load_rows(conn, begin, stop, {tid: rooms} if rooms else None)
        if emporia_gate:
            gate_with_emporia(conn, rows, tid, begin, stop)
        s = prof.samples(rows, tid, begin, stop, min_duration)
        for k in prof.TYPES:
            for x, v in s[k].items():
                binned[k][x] += v
    out = prof.profile(binned, prof.HEAT_TYPE[tid])
    out["samples"] = {k: sum(len(v) for v in binned[k].values()) for k in prof.TYPES}
    return out


def upstairs_rooms(conn, spans: list[tuple[date | datetime, date | datetime]], emporia_gate: bool = False) -> dict:
    return generate(conn, config.UP_ECOBEE_ID, spans, UPSTAIRS_ROOMS, emporia_gate, ROOM_AVERAGE_MIN_RESIST_MINUTES)
