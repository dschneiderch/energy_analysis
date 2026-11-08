# Whole-house profile options (for the insulation analysis)

Written 2026-11-07 to pick up later. Context: `src/energy/calc/profile.py` is a validated port of
beestat's per-thermostat temperature profiles (see `beestat-temperature-profiles.md`); keep it
unchanged so it stays comparable with beestat. These options go in a **new module** (for example
`src/energy/calc/house_profile.py`, plus `energy_signature.py` for option 3).

Insulation work: part 1 (crawlspace foam) on 2025-11-24, part 2 (attic) on 2025-12-01. Use
windows ending 2025-11-23 (before) and starting 2025-12-02 (after). Results and their
interpretation: `docs/summaries/insulation-effect.md`.

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

### Comparing periods

- `heating_season(start, end)` returns the October–May parts of a date range. Monthly runtime shows
  no heating outside them.
- `generate(conn, tid, spans, rooms)` pools several spans into one set of bins, so a split heating
  season counts as one period.
- **"±"** in the results is 2 standard errors of the per-bin differences. That's a rough guide that
  ignores correlation between bins, so treat it as optimistic.

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
  - An hour counts as "on" unless **both** heat-pump channels have a reading. 2024-12-01 to
    2025-04-15 has no hours with only one channel, so this doesn't affect that winter.
  - The gate replaces the Living Room data for the whole period, even where real readings exist:
    672 five-minute readings from 2024-12-01 to 2024-12-03 (end of the Living Room portal export).
    That keeps the whole pre-insulation winter on one method.
- **Validation** on the post-insulation heating season, where the true 5-minute state is known:
  the gate keeps 42 % of the room average's resist samples (1,861 of 4,426, 15-minute minimum) and
  reads 0.02 ± 0.04 °F/h faster loss over 26 bins. It loses the bins below 27 °F.
- **Compressor-off hours in 2024-12-01 to 2025-04-15, by outdoor temperature:** 0 of 45 below
  10 °F; 1 of 96 in the 10s; 26 of 652 in the 20s; 120 of 1,453 in the 30s; 178 of 659 in the 40s.
  The cold snaps can't be covered: the heat pump is never off for a whole hour then.
- **No second pre-insulation winter with room readings**, so natural variation for comparisons
  that use the gate can't be measured.

## Why per-thermostat profiles are weak here

- One heat pump serves both zones. The Living Room thermostat drives it as a heat pump. Upstairs is
  configured as non-heat-pump, so its heat calls show up as aux.
- beestat's heat samples need the *other* thermostat off. That is rare, so Upstairs had only 38
  heating samples in Apr–Nov 2025.
- beestat applies the profiled thermostat's heat type to every row. For Upstairs, Living Room heating
  doesn't count as "on", which contaminates Upstairs resist samples.
- Year-to-year variation in the cold-range bins (±0.1–0.5 °F/h) is as large as the before/after
  changes being measured (`docs/summaries/insulation-effect.md`).
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
