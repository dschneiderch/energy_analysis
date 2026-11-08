# Whole-house profile options (for the insulation analysis)

Written 2026-11-07 to pick up later. Context: `src/energy/calc/profile.py` is a validated port of
beestat's per-thermostat temperature profiles (see `beestat-temperature-profiles.md`); keep it
unchanged so it stays comparable with beestat. These options go in a **new module** (for example
`src/energy/calc/house_profile.py`, plus `energy_signature.py` for option 3).

Insulation work. Use windows ending 2025-11-23 (pre) and starting 2025-12-02 (post).

- **Part 1, 2025-11-24: spray foam on the crawlspace walls and rim joist.** Expected signal: the
  Living Room thermostat's Basement and crawlspace sensors.
- **Part 2, 2025-12-01: attic.** Expected signal: the three Upstairs rooms losing heat more slowly
  (the user's impression is that they do).

Order confirmed on the user's calendar 2026-11-07.

## Implemented: `src/energy/calc/house_profile.py` (2026-11-07)

Same sampling rules as `profile.py`, with two changes:

- **Each thermostat is mapped by its own heat type.** This removes beestat's Upstairs quirk: Living
  Room compressor heat counts as "on" (beestat doc, heat normalization).
- **Optional fixed room average** as the indoor temperature, from `ecobee_sensor`. The sample needs
  every listed room's reading; otherwise indoor is unknown. Comfort-setting changes no longer trim 30
  minutes, because the room set doesn't change.
- **Resist minimum of 15 minutes for the room average** (`ROOM_AVERAGE_MIN_RESIST_MINUTES`; beestat
  and the default: 30). Mean resist over the same bins ≤ 55 °F, heating season, pre / post:

  | | 30 min | 20 min | 15 min |
  | --- | --- | --- | --- |
  | 3-room average | −0.41 / −0.49 | −0.41 / −0.47 | −0.40 / −0.47 |
  | Bedroom | −0.30 / −0.35 | −0.30 / −0.34 | −0.28 / −0.34 |
  | Kids' Room | −0.52 / −0.58 | −0.60 / −0.66 | −0.59 / −0.67 |
  | Office | −0.38 / −0.48 | −0.28 / −0.34 | −0.23 / −0.29 |
  | Upstairs zoneAveTemp | −0.53 / −0.57 | −0.60 / −0.58 | −0.60 / −0.62 |
  | Living Room zoneAveTemp | −0.85 / −0.95 | −0.93 / −1.15 | −1.07 / −1.29 |

  The average moves ≤ 0.02 °F/h and gets about twice the samples. Single rooms and zoneAveTemp
  shift by up to 0.2 °F/h, already at 20 minutes. That's probably post-cycle coasting near vents:
  some rooms keep cooling or warming right after the heat pump stops. The average cancels it, so
  only the average uses 15; everything else keeps 30.

`upstairs_rooms(conn, spans, emporia_gate=False)` uses Bedroom, Kids' Room and Office Guest Room. It leaves out the
Upstairs thermostat's own sensor, which sits where it responds most to the Living Room zone. Why
this matters: `zoneAveTemp` (the thermostat's reported indoor temperature) follows the comfort
setting's room. It is within 0.2 °F of the Kids' Room in Sleep and Weekend Home, and of the Office
in Home.

### Upstairs results (bins ≤ 55 °F)

Heating season only (October 1 – May 31; `house_profile.heating_season`), using
`generate(conn, tid, spans, rooms)`, which pools several spans into one set of bins:

- **Pre:** 2025-04-16 to 2025-05-31 and 2025-10-01 to 2025-11-23.
- **Post:** 2025-12-02 to 2026-05-31 and 2026-10-01 to 2026-11-07.
- **"±"** is 2 standard errors of the per-bin differences. That's a rough guide that ignores
  correlation between bins, so treat it as optimistic. There is no second pre-insulation heating
  season in the sensor data to measure natural variation.

| Resist, bins ≤ 55 °F | pre → post (°F/h) | resist samples pre / post |
| --- | --- | --- |
| Upstairs 3 rooms (15-min minimum) | −0.44 → −0.35 (+0.09 ± 0.03; 24 bins, 32–55 °F) | 2,307 / 4,426 |
| Upstairs Bedroom | −0.30 → −0.24 (+0.07 ± 0.05) | 1,158 / 2,014 |
| Upstairs Kids' Room | −0.52 → −0.47 (+0.06 ± 0.05) | same |
| Upstairs Office | −0.38 → −0.33 (+0.05 ± 0.05) | same |
| Upstairs zoneAveTemp (own-type mapping) | −0.53 → −0.47 (+0.05 ± 0.03) | 1,152 / 1,936 |
| Living Room zoneAveTemp | −0.85 → −0.69 (+0.16 ± 0.11; 20 bins, 35–55 °F) | 1,025 / 1,915 |

Positive change = slower heat loss.

- **Every room and both thermostats lose heat more slowly after the work:** about 20 % for the
  Upstairs room average (−0.44 → −0.35) and 19 % for the Living Room. Each change is larger than the
  rough uncertainty. Upstairs matches the attic (part 2); the Living Room zone sits above the foamed
  crawlspace (part 1).
- **Season mix remains, and it matters:** pre is spring and fall only, while post includes
  December–March. Upstairs winter resist reads about 0.1 °F/h *slower* than spring/fall resist at
  the same outdoor temperature, even with both periods pre-insulation (next section). That's as
  large as the effect above, so this comparison overstates it. Use the winter-to-winter comparison
  below.
- **Expected size:** a 20 % lower heat-loss coefficient should change Upstairs resist by about
  +0.08 °F/h at these temperatures, close to what is seen.
- **Heat_1 can't be judged yet.**
  - Upstairs has only 59 heating samples before the work. The room-average heating rate came out
    0.47 °F/h lower after the work (±0.43).
  - Living Room heating came out +0.28 ± 0.35 °F/h, which points the right way but is within noise.
  - Upstairs zoneAveTemp heating "drops" by 2.4 °F/h. That's the reading switching rooms, not the
    house.

### Pre-insulation winter for Upstairs: portal sensors + Emporia gate (2026-11-07)

- **Room sensors before the API data:** `parse_portal_csv` now also reads the remote-sensor columns
  after `DM Offset` into `ecobee_sensor`: `<room> (F)` as temperature, `<room> (%RH)` as humidity,
  `<room>2` as occupancy. API rows win at the same timestamps. Upstairs room temperatures now cover
  2024-12-01 to 2025-04-15 (37,197 intervals per room) as well as the API period.
- **No Living Room data for that winter**, so beestat's "all off" rule can't be checked directly.
  `generate(..., emporia_gate=True)` replaces the other thermostat with a stand-in that is "on" in
  every hour the heat pump (channels 7 + 8) drew ≥ `HP_OFF_KWH` = 0.08 kWh, or has no Emporia
  reading.
  - The threshold comes from 2025-04 to 2026-11: standby draws about 0.034 kWh/h, 99 % of hours
    with both thermostats off draw < 0.071, and 99 % of hours with ≥ 5 min of runtime draw ≥ 0.105.
  - Only whole compressor-off hours can then produce resist samples. Upstairs heating samples
    disappear, because Upstairs heating is the compressor.
- **Validation** on the post-insulation heating season, where the true 5-minute state is known:
  the gate keeps 42 % of the room average's resist samples (1,861 of 4,426, 15-minute minimum) and
  reads 0.02 ± 0.04 °F/h faster loss over 26 bins. It loses the bins below 27 °F.
- **Compressor-off hours in 2024-12-01 to 2025-04-15, by outdoor temperature:** 0 of 45 below
  10 °F; 1 of 96 in the 10s; 26 of 652 in the 20s; 120 of 1,453 in the 30s; 178 of 659 in the 40s.
  The cold snaps can't be covered: the heat pump is never off for a whole hour then.

Upstairs resist, all Emporia-gated, bins ≤ 55 °F (positive = slower heat loss). The 3-room rows
use the 15-minute minimum, the single-room rows 30 minutes:

| Comparison | °F/h | samples |
| --- | --- | --- |
| 3 rooms: pre winter 2024-12-01..2025-04-15 → pre shoulder 2025 (season effect, no insulation) | −0.28 → −0.39 (−0.11 ± 0.04; 19 bins 37–55 °F) | 422 → 1,129 |
| 3 rooms: pre winter → post heating season | −0.41 → −0.40 (+0.01 ± 0.06) | 422 → 1,861 |
| **3 rooms: pre winter → post winter 2025-12-02..2026-04-15** | **−0.40 → −0.34 (+0.06 ± 0.06; 25 bins 27–55 °F)** | 422 → 657 |
| Bedroom, winter → winter | −0.35 → −0.23 (+0.12 ± 0.07) | |
| Kids' Room, winter → winter | −0.45 → −0.36 (+0.08 ± 0.08) | |
| Office, winter → winter | −0.47 → −0.40 (+0.06 ± 0.07) | |

- **Winter to winter is the comparison to use.** Same months, same method on both sides. The
  three Upstairs rooms lose heat about 15 % more slowly after the attic work (+0.06 ± 0.06), down to
  27 °F. That's within the rough uncertainty: with 30-minute samples (296 → 427) it read
  +0.09 ± 0.07, so part of that was small-sample noise. Each room points the same way; the Bedroom
  is clearest (+0.12 ± 0.07).
- Comparisons that mix seasons mislead in both directions: winter alone reads slower loss than
  spring and fall (by 0.11 °F/h before the work).
- Not available: a second pre-insulation winter with room sensors, to measure natural variation.

### Next steps for the insulation signal

- **Part 1 (crawlspace):** Basement and crawlspace sensor temperatures from 2025-04-21. The
  Living Room portal export also covers 2024-11-01 to 2024-12-02, now imported. Compare their
  offset from outdoor temperature, or their drift with the heat pump off, before 2025-11-24 and
  after 2025-12-01. The week between them (crawlspace done, attic not yet) could isolate the
  crawlspace effect, but a week is a small sample.
- **Winter 2026-27** adds a second post-insulation winter, ideally a cold one, to firm up the
  winter-to-winter result.

## Why per-thermostat profiles are weak here

- One heat pump serves both zones. The Living Room thermostat drives it as a heat pump. Upstairs is
  configured as non-heat-pump, so its heat calls show up as aux.
- beestat's heat samples need the *other* thermostat off. That is rare, so Upstairs had only 38
  heating samples in Apr–Nov 2025.
- beestat applies the profiled thermostat's heat type to every row. For Upstairs, Living Room heating
  doesn't count as "on", which contaminates Upstairs resist samples.
- Year-to-year variation in the cold-range bins (±0.1–0.5 °F/h) is as large as any pre/post change
  seen so far (beestat doc, "Insulation check").
- Our 5-minute data with both thermostats only starts 2025-04-16, so there is no pre-insulation
  winter in it. beestat has deleted its older 5-minute data.

## Option 1: whole-house beestat-style profile

Treat the house as one virtual thermostat and reuse the sampling and aggregation code from
`profile.py` (binning, `remove_outliers`, `trendline`).

- **Indoor temperature:** the mean of Living Room and Upstairs `indoor_temp_f`, optionally weighted
  by conditioned floor area.
- **Heating on:** the compressor heating for either zone (Living Room `comp_heat_s` > 0, or Upstairs
  `aux_heat_s` > 0).
- **Strips:** Living Room `aux_heat_s` after `STRIP_INSTALL_DATE`. They have essentially never run;
  exclude those intervals, as beestat excludes aux from heat_1.
- **Resist:** both zones off. That is the same as beestat's rule, but without the Upstairs
  heat-type quirk.
- **Split heating samples** into "one zone calling" and "both calling". When one zone calls, the
  other keeps drifting, which drags the house average.
- **Pros:** far more heating samples, units you already know (°F/h vs outdoor temperature), and a
  direct comparison with the beestat charts.
- **Cons:** still thermostat-only data, so the same weather and setpoint noise. It needs data with
  both thermostats, which means 2025-04-16 onward (no pre-insulation winter).

## Option 2: resist normalized by indoor − outdoor (time constant)

For each resist sample (same rules as beestat), fit indoor change rate = −k × (indoor − outdoor),
pooling all samples in a window, so one k (1/hour) per period.

- This removes the setpoint effect: beestat bins by outdoor temperature only, so a warmer house
  loses heat faster at the same outdoor temperature.
- Add optional covariates (wind speed from `weather_hourly`, a night-only filter against solar gain)
  to cut noise.
- Bootstrap over days to get a confidence interval, so "significant" has a definition.
- This is the cleanest single thermostat-based insulation number, and a small amount of code.

## Option 3: energy signature from Emporia (strongest evidence)

Model daily heating energy against weather, PRISM-style:

    kWh_day = base + slope × max(0, T_balance − T_outdoor_mean)

- **kWh:** heat pump channels 7 + 8 plus strip kWh (`day_energy`), on heating days. Exclude
  `not_home` days and use the report's runtime basis.
- **Balance temperature** fitted by grid search (typically 55–65 °F), or use degree-days from
  `weather_hourly` with indoor temperatures from `ecobee_daily.avg_indoor_f`, which covers 2022 on.
- **slope** ≈ UA / COP, in kWh per °F·day. It's the same heat pump before and after, so compare
  slopes over the same outdoor-temperature range (COP falls in the cold). Or fit per
  outdoor-temperature bin.
- **Pros:** Emporia hourly goes back to 2023-04-15, so **two pre-insulation winters** (2023-24
  with −15 °F, and 2024-25 with −3 °F). It doesn't depend on thermostat configuration (the
  2023-24 aux period, the 2024-03 beestat reset) or on 5-minute data. It also measures what the
  insulation should change: energy.
- **Cons:** COP varies with outdoor temperature, and winter 2025-26 was mild, so the cold-range
  overlap is limited. Internal gains and occupancy add noise.
- **Significance:** compare the pre-insulation winters with each other first, to measure natural
  variation, then compare post against pre.

## Suggested order

1. Option 3 as the headline answer, with its year-to-year variation check.
2. Option 2 as a thermostat-side confirmation (small amount of code).
3. Option 1 if you want beestat-style charts for the whole house.
