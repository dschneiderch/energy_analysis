"""Temperature profiles from ecobee 5-minute data, computed like beestat's `profile::generate`
(docs/research/beestat-temperature-profiles.md). Only the temperature part: deltas, trendlines,
balance points. Stage-2 equipment is left out because these thermostats have none."""
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from energy import config

GROUP = (config.LR_ECOBEE_ID, config.UP_ECOBEE_ID)
# beestat's reported heat equipment. Its normalization applies the profiled thermostat's type to every
# row in the group, so for Upstairs the Living Room compressor heat does not count as "on" (beestat quirk).
HEAT_TYPE = {config.LR_ECOBEE_ID: "compressor", config.UP_ECOBEE_ID: "electric"}
TYPES = ("heat_1", "auxiliary_heat_1", "cool_1", "resist")  # beestat's order: only one sample ends per step
MIN_DURATION = {"heat_1": 300, "auxiliary_heat_1": 300, "cool_1": 300, "resist": 1800}
STEP, LOOK = 300, 1800
_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _php_round(x: float, digits: int = 0) -> float:
    """Round half away from zero, as PHP does (Python rounds half to even)."""
    m = 10 ** digits
    return math.copysign(math.floor(abs(x) * m + 0.5), x) / m


def load_rows(conn, begin: int, end: int, heat_type: str) -> dict[int, dict[str, dict]]:
    """Group rows by epoch second, normalized like beestat's runtime_thermostat table."""
    out: dict[int, dict[str, dict]] = defaultdict(dict)
    start = datetime.fromtimestamp(begin - LOOK, timezone.utc).strftime(_FMT)
    stop = datetime.fromtimestamp(end + LOOK, timezone.utc).strftime(_FMT)
    for tid, ts, ch, cc, ah, indoor, outdoor, mode, event, climate in conn.execute(
            "SELECT thermostat_id, ts, comp_heat_s, comp_cool_s, aux_heat_s, indoor_temp_f, outdoor_temp_f, "
            "hvac_mode, calendar_event, climate FROM ecobee_interval "
            f"WHERE thermostat_id IN ({','.join('?' * len(GROUP))}) AND ts >= ? AND ts < ?", (*GROUP, start, stop)):
        if mode is None or indoor is None or None in (ch, cc, ah) or (outdoor is not None and outdoor < -1000):
            continue  # beestat drops these rows
        comp_mode, comp = ("cool", cc) if cc > 0 else ("heat", ch) if ch > 0 else ("off", 0)
        if heat_type == "compressor":
            heat, aux = (comp if comp_mode == "heat" else 0), ah
        else:
            heat, aux = ah, 0
        t = int(datetime.strptime(ts, _FMT).replace(tzinfo=timezone.utc).timestamp())
        out[t][tid] = {"ts": t, "indoor": indoor, "outdoor": outdoor, "heat_1": heat, "auxiliary_heat_1": aux,
                       "cool_1": comp if comp_mode == "cool" else 0, "event": event, "climate": climate}
    return out


def samples(rows: dict[int, dict[str, dict]], tid: str, begin: int, end: int,
            min_duration: dict[str, int] = MIN_DURATION) -> dict[str, dict[int, list[float]]]:
    """°F/hour indoor change per sample, by type and whole-degree outdoor temperature at the sample start."""
    first = {g: min((t for t, r in rows.items() if g in r), default=None) for g in GROUP}
    out: dict[str, dict[int, list[float]]] = {k: defaultdict(list) for k in TYPES}
    starts: dict[str, dict] = {}
    on_for = {k: 0 for k in TYPES if k != "resist"}
    all_off_for, cur, previous = 0, None, None
    for t in range(begin, end + 1, STEP):
        at = rows.get(t, {})
        if tid in at:
            cur = dict(at[tid])
            if cur["outdoor"] is not None:
                cur["outdoor"] = int(_php_round(cur["outdoor"]))
            relevant = [g for g in GROUP if first[g] is None or first[g] <= t]
            all_off = most_off = len(at) >= len(relevant)
            if all_off:
                for g, r in at.items():
                    if r["heat_1"] or r["auxiliary_heat_1"] or r["cool_1"] or r["outdoor"] is None or r["indoor"] is None:
                        all_off = False
                        if g != tid:
                            most_off = False
            if all_off:
                all_off_for += STEP
                if all_off_for == STEP:
                    starts["resist"] = cur
            else:
                all_off_for = 0
            for k in on_for:
                on_for[k] = on_for[k] + cur[k] if cur[k] > 0 else 0
            if most_off and on_for["heat_1"] >= 300 and cur["auxiliary_heat_1"] == 0:
                starts.setdefault("heat_1", cur)
            if most_off and on_for["auxiliary_heat_1"] >= 300:
                starts.setdefault("auxiliary_heat_1", cur)
            if most_off and on_for["cool_1"] >= 300:
                starts.setdefault("cool_1", cur)

            for k in TYPES:
                b = starts.get(k)
                if b is None or previous is None:
                    continue
                if (cur["outdoor"] == b["outdoor"] and cur["event"] == b["event"] and cur["climate"] == b["climate"]
                        and (all_off if k == "resist" else most_off)):
                    continue
                # Comfort-setting changes swap the sensors behind zoneAveTemp: end 30 min before them.
                offset = STEP
                if cur["event"] != b["event"] or cur["climate"] != b["climate"]:
                    offset = LOOK
                else:
                    for ahead in range(STEP, LOOK, STEP):
                        nxt = rows.get(t + ahead, {}).get(tid)
                        if nxt and (nxt["event"] != cur["event"] or nxt["climate"] != cur["climate"]):
                            offset = LOOK - ahead
                            break
                end_row = rows.get(t - offset, {}).get(tid)
                if end_row and t - offset > b["ts"]:
                    duration = end_row["ts"] - b["ts"]
                    if duration >= min_duration[k]:
                        out[k][b["outdoor"]].append((end_row["indoor"] - b["indoor"]) / duration * 3600)
                starts[k] = cur
                break
            previous = cur

        c = cur or {}
        temps_missing = c.get("outdoor") is None or c.get("indoor") is None
        if on_for["heat_1"] == 0 or temps_missing or (c.get("auxiliary_heat_1") or 0) > 0:
            starts.pop("heat_1", None)
        if on_for["auxiliary_heat_1"] == 0 or temps_missing:
            starts.pop("auxiliary_heat_1", None)
        if on_for["cool_1"] == 0 or temps_missing:
            starts.pop("cool_1", None)
        if all_off_for == 0:
            starts.pop("resist", None)
    return out


def trendline(deltas: dict[int, float]) -> dict | None:
    if len(deltas) < 2:
        return None
    n, sx, sy = len(deltas), sum(deltas), sum(deltas.values())
    sxy, sxx = sum(x * y for x, y in deltas.items()), sum(x * x for x in deltas)
    slope = (n * sxy - sx * sy) / (n * sxx - sx ** 2)
    return {"slope": _php_round(slope, 4), "intercept": _php_round((sy - slope * sx) / n, 4)}


def remove_outliers(deltas: dict[int, float]) -> dict[int, float]:
    """Drop bins whose distance from the fitted line is outside mean ± 2 SD of all distances."""
    line = trendline(deltas)
    spread = {x: abs(y - (line["slope"] * x + line["intercept"])) for x, y in deltas.items()}
    mean = sum(spread.values()) / len(spread)
    sd = _php_round(math.sqrt(sum((s - mean) ** 2 for s in spread.values()) / len(spread)), 2)
    return {x: y for x, y in deltas.items() if mean - 2 * sd <= spread[x] <= mean + 2 * sd}


def profile(binned: dict[str, dict[int, list[float]]], heat_type: str) -> dict:
    temperature: dict[str, dict | None] = {}
    for k in TYPES:
        deltas = {x: _php_round(median(v), 2) for x, v in sorted(binned[k].items()) if len(v) >= 2}
        if len(deltas) < 5:
            temperature[k] = None
            continue
        deltas = remove_outliers(deltas)
        temperature[k] = {"deltas": deltas, "linear_trendline": trendline(deltas)}

    def balance(k):
        line = (temperature.get(k) or {}).get("linear_trendline")
        return _php_round(-line["intercept"] / line["slope"], 1) if line and line["slope"] > 0 else None
    return {"temperature": temperature,
            "balance_point": {"heat_1": balance("heat_1") if heat_type == "compressor" else None,
                              "resist": balance("resist")}}


def _instant(d: date | datetime, next_day: bool = False) -> datetime:
    if isinstance(d, datetime):
        return d
    return datetime.combine(d + timedelta(days=next_day), time.min, ZoneInfo(config.LOCAL_TZ))


def window(start: date | datetime, end: date | datetime) -> tuple[int, int]:
    """Epoch seconds of the first and last 5-minute step. Dates are whole local days, both included."""
    return int(_instant(start).timestamp()) // STEP * STEP, int(_instant(end, next_day=True).timestamp()) // STEP * STEP


def generate(conn, tid: str, start: date | datetime, end: date | datetime) -> dict:
    """beestat-style profile from `start` to `end`. Dates are whole local days, both included.
    beestat's own window is the year before its generation time: start = generated_at − 1 year."""
    begin, stop = window(start, end)
    binned = samples(load_rows(conn, begin, stop, HEAT_TYPE[tid]), tid, begin, stop)
    out = profile(binned, HEAT_TYPE[tid])
    out["samples"] = {k: sum(len(v) for v in binned[k].values()) for k in TYPES}
    return out
