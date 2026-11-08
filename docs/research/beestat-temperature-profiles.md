# beestat temperature profiles: how they are made

Research for the insulation-impact sub-project, 2026-11-07. Source: beestat's open-source app
([beestat/app](https://github.com/beestat/app), GPL-3.0), `api/profile.php` (`profile::generate`, last
changed 2026-10-23) and `api/runtime.php` (how ecobee rows are stored). Goal: compute profiles from our
own ecobee data that can be compared with beestat's.

## What a profile is

For each thermostat, beestat estimates how fast the indoor temperature changes (°F per hour) at each
whole-degree outdoor temperature, separately for these modes:

- `heat_1`, `heat_2`: compressor or furnace stage heating, with no aux heat running
- `auxiliary_heat_1`, `auxiliary_heat_2`
- `cool_1`, `cool_2`
- `resist`: every thermostat in the house off. The house drifting toward outdoor temperature is its heat loss.

Each mode gets a `deltas` map (outdoor °F → median °F/hour) and a straight-line fit
(`linear_trendline`: slope, intercept). Balance point = −intercept / slope, reported only when the
slope is positive (for `resist`: the outdoor temperature at which the house neither gains nor loses heat).

## When profiles are made

- `thermostat.sync` calls `profile.generate` for each thermostat. One profile is kept per thermostat
  per week. A regeneration in the same week **overwrites** that week's profile (same `profile_id`,
  later `date`).
- The window is the **trailing 365 days** from generation time. A per-thermostat `range_begin` user
  setting can shorten it; it is unset on this account. Early profiles with less data report a
  shorter `metadata.duration` (for example 177 days).
- `profile.read_id` is public (commit "Exposed profiles in the API", 2023-12) but not in the
  Notion API docs. We archive it; see `data_archive/beestat/profile_*.json` and the
  `beestat_profile` table.

## Input rows (`runtime.php`)

beestat stores ecobee `runtimeReport` rows with these changes:

| beestat field | from ecobee | our `ecobee_interval` column |
|---|---|---|
| `indoor_temperature` | `zoneAveTemp` | `indoor_temp_f` |
| `outdoor_temperature` | `outdoorTemp` (dropped if < −100 °F) | `outdoor_temp_f` |
| `compressor_mode` + `compressor_1` | `compCool1` > 0 → cool; else `compHeat1` > 0 → heat; else off | `comp_cool_s`, `comp_heat_s` |
| `auxiliary_heat_1` | `auxHeat1` | `aux_heat_s` |
| `system_mode` | `HVACmode` | `hvac_mode` |
| `event_runtime_thermostat_text_id` | `zoneCalendarEvent` | `calendar_event` |
| `climate_runtime_thermostat_text_id` | `zoneClimate` | `climate` |
| `setpoint_heat`, `setpoint_cool` | `zoneHeatTemp`, `zoneCoolTemp` | `heat_setpoint_f`, `cool_setpoint_f` |

A whole row is **dropped** if any of these is null: `HVACmode`, `zoneAveTemp`, `zoneHumidity`,
`compHeat1/2`, `compCool1/2`, `auxHeat1/2`, `fan`, `humidifier`, `dehumidifier`, `ventilator`,
`economizer`, or if `auxHeat2 > auxHeat1`. We do not request humidity or the accessory columns. Our
equivalent is to drop rows with null `hvac_mode`, `indoor_temp_f` or runtime columns; the
humidity/accessory nulls are rare according to beestat's own comment.

Per-thermostat heat normalization: if the thermostat's heat system type is `compressor` or
`geothermal`, `heat_1` = compressor runtime while `compressor_mode` is heat. Otherwise (furnace,
electric), `heat_1` = `auxiliary_heat_1` and aux is set to 0. This is why the Upstairs thermostat
(configured as non-heat-pump, compressor calls logged as aux) gets a usable `heat_1` profile.
beestat's reported types here: Living Room `compressor`, Upstairs `electric`.

Quirk: the *profiled* thermostat's type is applied to every row in the group. For the Upstairs
profile, Living Room compressor heating therefore becomes `heat_1` = Living Room aux (0), so it
does not count as "on". `profile.py` copies this to stay comparable; `house_profile.py` maps each
thermostat by its own type. Measured effect on Upstairs (bins ≤ 55 °F, 2026-11-07):

| Upstairs | beestat quirk | each thermostat by its own type | bias |
| --- | --- | --- | --- |
| resist, Apr 16 – Nov 23 2025 | −0.56 °F/h, 3,079 samples | −0.53, 2,758 | −0.03 |
| resist, Dec 2 2025 – Nov 7 2026 | −0.72 °F/h, 4,663 samples | −0.58, 3,496 | −0.14 |
| heat_1, Dec 2 2025 – Nov 7 2026 | +2.61 °F/h, 220 samples | +2.47, 275 | +0.14 |

- The quirk adds 10–25 % extra resist samples: periods when the heat pump heats the Living Room.
  With them, Upstairs appears to lose heat *faster*. That is probably because those are the colder
  stretches (not checked).
- The bias grows with how much the Living Room heats, so it differs between periods (−0.03 in a
  mostly warm window, −0.14 in one with winter). That's larger than the pre/post changes being
  measured. **beestat's archived Upstairs resist can't be used for a pre/post comparison**, and it
  can't be corrected, because the 5-minute data is gone.
- Upstairs heat_1 is usable with a bias of about 0.1 °F/h. It includes times when both zones call at
  once.
- The Living Room profile is not affected: under the compressor mapping, Upstairs aux counts as "on".
- A second problem for Upstairs: `zoneAveTemp` follows the room of the current comfort setting
  (Sleep and Weekend Home → Kids' Room, Home → Office). The thermostat's own sensor tracks the Living
  Room zone because of where it sits. See `house-profile-options.md` for the room-average profile.

## Algorithm (`profile::generate`)

Walk the window in 5-minute steps over all *active thermostats at the same address* (the "group":
Living Room and Upstairs). Rows are treated as the state at the **end** of each 5 minutes.

1. **Group state at each step**
   - `all_off`: every group thermostat has a row at this timestamp, and none has any heat, cool or
     aux runtime, and none has a null indoor or outdoor temperature.
   - `most_off`: the same, but ignoring the requested thermostat (everything *else* is off).
   - A timestamp missing a row for any thermostat (one whose `data_begin` is already past) makes
     both false.
2. **Sample start** (stored as `begin_runtime[type]`):
   - `resist`: on the first step with `all_off` after 5 minutes of continuous all-off.
   - `heat_1`: requested thermostat's `heat_1` on for ≥ 5 minutes, `most_off`, no aux on.
   - `auxiliary_heat_1`: aux on for ≥ 5 minutes, `most_off` (compressor heat may run with it).
   - `cool_1`: cool on ≥ 5 minutes, `most_off`, `cool_2` off.
3. **Sample end**: a running sample ends at the first step where any of these happens:
   - The outdoor temperature (rounded to whole °F) differs from the start's.
   - The calendar event or the climate (comfort setting) differs from the start's.
   - Another thermostat turns on (for heat/cool), or anything turns on (for resist).
4. **End point adjustment**: normally the sample's last row is the previous step (5 minutes back).
   - If the event or climate changed, the end moves back **30 minutes**, because `zoneAveTemp` mixes
     different sensors per comfort setting and the average shifts when the setting changes.
   - Otherwise it looks ahead up to 30 minutes for an upcoming event/climate change and pulls the
     end back by the same margin.
   - If the end row is missing or not after the start, the sample is discarded (no further search).
5. **Minimum duration**: 5 minutes for heat/aux/cool, **30 minutes** for resist. Shorter samples are
   discarded. The code comments note longer minimums bias resist toward calm, sunny periods.
6. **Sample value**: `delta_per_hour = (indoor_end − indoor_start) / duration × 3600`, filed under the
   outdoor temperature at the sample **start**, rounded to whole °F.
7. A new sample of the same type starts immediately from the current row when it is still valid.
   Starts are cleared when the mode stops, a temperature is null, or (for heat) aux turns on.
8. **Per-temperature value**: the **median** `delta_per_hour` of each outdoor-temperature bin, kept
   only if the bin has **≥ 2 samples**, rounded to 0.01 °F/hour.
9. A mode gets a profile only if it has **≥ 5 temperature bins**.
10. **Outlier removal**: fit an unweighted least-squares line through the bins (each bin counts once,
    whatever its sample count). Compute each bin's absolute distance from the line. Drop bins whose
    distance is outside mean ± 2 standard deviations of those distances. Then fit the reported line
    on the remaining bins. A quirk: when mean − 2 SD is positive, bins lying unusually *close* to the
    line are dropped too.
11. **Optional solar filter** (`ignore_solar_gain` setting, off on this account): skip rows between
    sunrise and sunset.

Other profile fields:

- `setpoint.heat/cool`: mean setpoint over rows in heat/auto (cool/auto) mode. The heat branch checks
  `setpoint_cool` for null instead of `setpoint_heat`, which looks like a copy-paste slip.
- `degree_days`: per calendar day (server time), the mean of all fetched outdoor temperatures (all
  group thermostats) minus 65 °F, then summed into heat and cool totals.
- `runtime` and `runtime_per_degree_day`: runtime minutes summed over **every row of every group
  thermostat**, using the *requested* thermostat's heat normalization, and including the 30-minute
  overlap rows that are read twice at chunk boundaries. These are **not per-thermostat**. For the
  Living Room, the Upstairs heat lands in `auxiliary_heat_1` (Upstairs logs its heat as aux).
  Don't use these numbers for per-zone comparisons.

## Our implementation and validation

`src/energy/calc/profile.py` ports the temperature part of `profile::generate`: deltas, trendlines
and balance points. It leaves out setpoints, degree days, runtime and setback, and stage-2
equipment, which these thermostats don't have. `generate(conn, ecobee_id, start, end)` takes dates
(whole local days, both included) or exact datetimes. For beestat's own window, use
`start = generated_at − 1 calendar year` and `end = generated_at`.

Validated 2026-11-07 against every stored beestat profile whose 365-day window lies inside our
5-minute data (generated 2026-04-23 to 2026-10-31, both thermostats, 12 profiles):

- July–October 2026 profiles: all heat_1 and cool_1 bins identical. Resist differs in at most 2 of
  about 70 bins, by 0.01 °F/h (likely rounding of halves). Trendlines agree to the 4th decimal and
  balance points within 0.1 °F.
- April–May 2026 profiles, whose windows begin right after our data starts on 2025-04-16: up to 10
  resist bins and 2 heat bins differ, mean difference ≤ 0.025 °F/h. The Living Room heat_1 balance
  point moves by up to 3 °F, but that line is nearly flat, so its balance point is not meaningful.
- Each profile takes about 2 s to compute.

## Metrics for the insulation analysis

- **Resist slope** (°F/h per °F outdoor): with heating off, indoor drift ≈ −k × (indoor −
  outdoor), so the slope estimates k, the heat-loss rate divided by the house's heat capacity.
  Lower = slower heat loss. The resist value at a fixed outdoor temperature (for example 40 °F) is
  the same idea in °F/h.
- **Heating line height** at a fixed outdoor temperature (°F/h): how fast the house warms while
  heating. The heating *slope* is how much that rate falls as it gets colder (more house loss plus
  less heat-pump output). Better insulation should raise the cold-weather end of the line.
- **Heating x-intercept** (beestat's heat balance point, −intercept / slope): the outdoor
  temperature at which heating can no longer raise the indoor temperature. Trust it only when it
  lies near the observed bin range. A nearly flat line puts it far outside (Living Room
  2026-10-31: −343 °F).

**Compare bins in the cold range, not whole-line slopes.** The fitted lines span roughly 20–95 °F,
so summer bins (with solar gain) drive the resist slope. The heating slope swings with whichever
cold snaps happen to be in the window: the Living Room heat_1 slope jumped from 0.003 to 0.034 when
the 2025-02 cold snap (−3 °F) entered the trailing window, and fell again when it left in 2026-02.
A first look that compared whole-line slopes over April–October 2025 vs 2026 showed −16 % (Living
Room) and −23 % (Upstairs) resist slopes. That turned out to be a fitting artifact (see below).

Insulation check (2026-11-07). Each value is the change in mean °F/h over the bins both profiles
share, all at or below 55 °F. It is compared with the difference between two pre-insulation
profiles:

| | Pre year → post year | Variation: 2024-11-19 → 2025-11-18 | Variation: 2025-03-01 → 2025-11-18 |
| --- | --- | --- | --- |
| Living Room resist | −0.90 → −0.88 (+0.02; 24–55 °F) | +0.10 | +0.09 |
| Upstairs resist | −0.84 → −0.74 (+0.10; 21–55 °F) | −0.15 | −0.07 |
| Living Room heat_1 | +3.12 → +3.30 (+0.17; 19–55 °F) | +0.41 | +0.16 |
| Upstairs heat_1 | +2.49 → +2.46 (−0.03; 20–47 °F) | −0.55 | −0.17 |

- **Pre year:** beestat's profile of 2025-11-18 (the year to 2025-11-18).
- **Post year:** ours, 2025-12-02 to 2026-11-07.
- **Variation 2024-11-19:** independent of the baseline. For the Living Room its window starts
  after the 2024-03 reset (about 2024-03-05 → 2024-11-19, no aux period). For Upstairs it covers
  2023-11 → 2024-11, including the Living Room aux winter.
- **Variation 2025-03-01:** clean, but its window (2024-03 → 2025-03) shares the whole 2024–25
  winter with the baseline, so it understates the true variation.

Bin ranges are where both periods have bins. There is no lower cutoff: the data
stops near 21 °F because resist needs both zones off for 30 minutes, which is rare in the cold, and
winter 2025–26 had no cold snap (Upstairs' 12–15 °F pre bins have no post counterpart). The 55 °F
upper cutoff is deliberate: above it, sun heating dominates. **Every pre→post change is within
the variation between pre-insulation profiles, even against the overlapping 2025-03-01 profile**,
so thermostat profiles alone don't show a significant insulation effect yet. Upstairs resist (+0.10 =
slower loss) points the right way but isn't conclusive. See `house-profile-options.md` for methods
with more statistical power.

## History of the archived profiles (what is usable)

From the 288 archived profiles and beestat's daily runtime:

- **Living Room aux period: November 2023 – March 2024.** The heat pump logged 18–234 aux
  hours a month, near zero after that (heat strips have essentially never run). Profiles from that
  winter (2023-12 to 2024-02) have an `auxiliary_heat_1` curve and a split heat_1. Don't use them.
- **History resets.** beestat's 5-minute history restarted around 2024-03-05 (Living Room: profile
  duration drops to 0) and 2024-03-21 (Upstairs: 21 days), right when the aux period ended,
  probably because of the reprogramming. Profiles until about 2025-03 cover less than a year
  (`metadata.duration` < 365).
- **Clean full-year pre-insulation profiles: generated 2025-03-01 to 2025-11-18.** These are full
  365-day windows, all after the 2024-03 reset, so none include the aux period, and they include the
  2025-02 cold snap. **The profile of 2025-11-18 is the best pre-insulation baseline** (part 1 of the
  work, crawlspace foam, was 2025-11-24; part 2, the attic, was 2025-12-01).
- The first beestat profile entirely after the work will be generated around 2026-12-02. Until then,
  use our own `generate()` for a post window.

## What this means for us

- **We have every input needed.** `ecobee_interval` holds the same ecobee columns for both
  thermostats from 2025-04-16 (API), plus portal-CSV months. Rows match beestat's timestamps (local
  time converted to UTC) and units (beestat stores tenths of °F and divides back out).
- **House-specific settings to copy:**
  - Group = Living Room (415533522899) + Upstairs (415522180035).
  - Living Room heat type = compressor.
  - Upstairs heat type = non-compressor, so its `heat_1` = aux runtime.
  - Living Room aux after `STRIP_INSTALL_DATE` are real strips, so they end `heat_1` samples, as in
    beestat.
- **Validation plan:** run our implementation on the 365 days ending at a stored beestat profile's
  `generated_at` (for example the Living Room profile of 2026-10-31) and compare the `deltas` bins and
  trendlines. Expect small differences from the row-drop rules above and from beestat's chunked reads.
- **Pre-insulation data:** beestat itself only keeps about a year of 5-minute data (`data_begin` is
  2025-11-01 for both live thermostats), and our 5-minute data with both thermostats starts
  2025-04-16. So the **archived beestat profiles** are the only profiles covering the full
  pre-insulation winter (for example the profiles dated around 2025-11). Keep archiving them.
- **Fixed windows instead of trailing years:** beestat's profiles always cover the trailing 365
  days, so a Nov/Dec 2025 change appears gradually over the next year. Our own implementation can
  run the same algorithm over fixed windows (for example heating season 2025-26 vs 2026-27, or the
  months before and after the work). Only a run on beestat's exact window is directly comparable
  with beestat's numbers.
- **Physics caveat:** `resist` bins by outdoor temperature only, so the indoor temperature (setpoint)
  also changes the drift rate. Heat loss scales with indoor − outdoor. For before/after comparisons
  across different setpoints, a delta-per-hour per °F of indoor − outdoor difference would be less
  biased. Report it alongside the beestat-compatible numbers, not instead of them.
