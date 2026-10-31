# Home Energy Data Integration — Design

Date: 2026-10-31
Status: approved in conversation, awaiting written-spec review

## 1. Goal and scope

The long-term goal is a complete look at home heating and cooling energy use: energy (kWh), dollars and carbon, including a heat pump vs. old gas furnace comparison, the effect of the Nov/Dec 2025 insulation projects, and cost/carbon hindcasting.

**This spec covers only the first sub-project:** pulling every data source into one local SQLite database, and a daily report command that proves the integration works. Later sub-projects (insulation impact, gas vs. heat pump comparison, hindcasting) get their own specs and read from this database.

The daily report takes a date and returns:

- heating, cooling and other kWh (heat pump circuits plus heat-strip energy)
- heat pump runtime (heating, cooling) and aux heat runtime
- HDD (and CDD) for the day
- % low-carbon electricity and g CO2e
- cost in $ at the Avista rate in effect that day
- for gas-furnace-era dates: therms, kWh-equivalent, gas cost and gas CO2e

If `energy day 2026-01-15` returns all of these, the integrations are working.

## 2. House and equipment facts

These come from the user and from the data exploration on 2026-10-31.

- One heat pump compressor serves two zones:
  - **Living Room** thermostat (ecobee id `415533522899`, beestat id `47724`): main floor. Configured as a heat pump.
  - **Upstairs** thermostat (ecobee id `415522180035`, beestat id `47723`): attic air handler for the bedrooms. Misconfigured as a non-heat-pump system, so its heat calls are logged in the aux/heat-stage column. They are compressor calls, not aux heat.
  - An older main thermostat ("My ecobee", beestat id `47722`) has data from 2022-11-25 to 2023-11-01. It covers the last gas-furnace winter and the first heat pump months.
- The heat pump started drawing power on **2023-04-15** (first Emporia reading). Before that date, all heating runtime is gas furnace runtime.
- Service techs misconfigured the thermostats several times, including an "aux" mode that didn't exist. **Living Room "aux" runtime before the heat strip installation is heat pump runtime.** This was confirmed in the 1-minute Emporia data: aux days have the same power shape as compressor-only days, with a ~5 kW maximum.
- **5 kW heat strips** were installed some time after March 2025 (exact date unknown). They are on a circuit with no Emporia sensor, so they only show up in mains. Aux runtime logged so far since installation is zero.
- The heat pump is Emporia circuits **7 and 8**. Channel 97 is Emporia's combined channel and equals 7 + 8 exactly, verified over 1,295 days.
- Floor heat (circuits 10/11), the gear room heater and the other circuits are stored, but they are **not** part of heating totals.

## 3. Data sources

| Source | Access | Coverage | Limits / notes |
|---|---|---|---|
| ecobee runtime report (5-min) | `python-ecobee-api` web login (username/password + authenticator code once, then refresh token) → `GET api.ecobee.com/1/runtimeReport` | rolling ~18 months (currently from 2025-04-16) | unofficial login route; ≤31 days per request; older data ages out daily |
| ecobee API backup | `data_archive/ecobee_api_runtime/*.json` (saved 2026-10-31, includes remote sensors) | 2025-04-16 → 2026-10-31 | already-fetched raw responses |
| ecobee portal CSV exports | `data_archive/ecobee_portal_csv/report-<thermostatId>-<start>-to-<end>.csv` (copied from `../insulation_impact_analysis/data/`) | Upstairs: Dec 2024–Apr 2025, Nov 8–Dec 9 2025, Jan 2026. Living Room: Nov 2024, Nov 2025, Jan 2026 | rows end in a trailing comma (read with `index_col=False`, header after 5 lines); values match the API on overlap |
| beestat daily summaries | `api.beestat.io` with `BEESTAT_API_KEY`, `runtime_thermostat_summary.read_id` | 47722: 2022-11-25 → 2023-11-01; 47723: 2023-03-25 →; 47724: 2023-10-30 → | daily sums of compressor heat/cool, aux, fan, outdoor and indoor temperature; matches the ecobee API (corr 0.994, mean diff 0.03 h/day). beestat's 5-min data only goes back about 13 months and is not used. API is officially "unstable"; ~30 requests/minute |
| Emporia Vue (device 219614) | PyEmVue with `keys.json` credentials | hourly kWh from 2023-04-15 for all channels including mains (`1,2,3`) | ≤90 days per request at daily scale, ≤30 days at hourly scale |
| Grid carbon (Electricity Maps zone `US-NW-AVA`) | Home Assistant WebSocket `recorder/statistics_during_period`, hourly `mean` of `sensor.electricity_maps_co2_intensity` and `sensor.electricity_maps_grid_fossil_fuel_percentage`, using `HA_TOKEN` | 2024-02 → now, ~complete | HA raw history only goes back ~10 days; use long-term statistics only |
| Weather | Open-Meteo archive API (`archive-api.open-meteo.com/v1/archive`), hourly `temperature_2m` (°F) and `wind_speed_10m` (mph), timezone UTC | any date | modeled reanalysis at the configured coordinates; archive lags a few days, so fall back to the forecast API (`past_days`) for recent dates and tag those rows |
| Avista rates | `rates.csv` (in git), hand-transcribed from Avista's Idaho rate-history PDFs: electric Schedule 1 (`ideratehist9126.pdf`) and gas Schedule 101 (`IDGRatehist9126.pdf`) | all rate changes covering 2021 → now | OpenEI is not used (no Idaho Avista data after 2018, no gas) |
| Gas use | `data_archive/avista_gas/`: `gas_furnace_therms_history.csv` (therms per day), plus raw `avista-gas-daily-*.csv` (CCF meter reads ~16:00) | Dec 2021 – May 2023 with gaps | |
| Not-home days | `exclude_dates.txt` in the project root (committed; the user keeps adding to it), plus ecobee vacation events | 2021-12 → present (file), plus events | vacation events are long numeric ids, hex ids, or "Vacation"; their heat setpoint is 55°F |

Secrets live in `.env` (`HA_TOKEN`, `ECOBEE_USERNAME`, `ECOBEE_PASSWORD`, `BEESTAT_API_KEY`) and `keys.json` (Emporia). Saved tokens go in `.secrets/`. All of these are gitignored.

## 4. Architecture

A Python package managed with `uv`.

```
src/energy/
  config.py        # paths, secrets loading, constants and settings (see §8)
  store.py         # SQLite schema, upserts, sync_log
  sources/
    ecobee.py      # login/refresh, runtimeReport fetch, import of API backup JSON and portal CSVs
    beestat.py     # daily summaries
    emporia.py     # hourly kWh, all channels
    ha_carbon.py   # hourly grid carbon from HA long-term statistics
    weather.py     # Open-Meteo hourly weather
    local_files.py # gas CSVs, exclude_dates.txt, rates.csv
  daily.py         # computes one day's report from the store only
  cli.py           # entry point `energy`
tests/
rates.csv
```

**Boundaries:**
- Only `sources/*` talks to external systems or reads input files. Each source module fetches raw data and writes it to the store.
- `daily.py` and all later analyses read only from SQLite, so they work offline.

**Commands:**
- `energy sync [SOURCE ...] [--since DATE]`: for each source, fills missing ranges from its earliest available date (or `--since`) up to today. Re-runs are safe and only fetch what's missing, apart from refreshing the most recent days.
- `energy ecobee-login`: interactive login. Prompts for the authenticator code and saves the refresh token.
- `energy day YYYY-MM-DD [--json]`: prints the daily report.

## 5. Storage (SQLite, `data/energy.sqlite`)

All timestamps are stored in UTC. A "day" is local midnight to midnight in `America/Los_Angeles`, so a daylight-saving day has 23 or 25 hours.

| Table | Primary key | Columns |
|---|---|---|
| `ecobee_interval` | thermostat_id, ts | comp_heat_s, comp_cool_s, aux_heat_s, fan_s, outdoor_temp_f, indoor_temp_f, heat_setpoint_f, cool_setpoint_f, hvac_mode, zone_hvac_mode, calendar_event, climate, source (`api` / `portal_csv`) |
| `ecobee_sensor` | thermostat_id, ts, sensor_name, kind | value |
| `ecobee_daily` | thermostat_id, date | comp_heat_s, comp_cool_s, aux_heat_s, fan_s, avg/min/max_outdoor_f, avg_indoor_f, interval_count, source (`beestat`) |
| `emporia_hourly` | channel, ts | kwh, channel_name |
| `grid_carbon_hourly` | ts | co2_g_per_kwh, fossil_pct |
| `weather_hourly` | ts | temp_f, wind_mph, source (`archive` / `forecast`) |
| `gas_daily` | date | therms, ccf, in_town |
| `rates` | fuel, schedule, effective_date, component, tier_min | rate_per_unit, unit (loaded from `rates.csv`) |
| `not_home` | start_date, end_date | source (`exclude_dates` / `ecobee_vacation`) |
| `sync_log` | source, range_start, range_end | fetched_at, rows, status |

**Thermostat ids:** `ecobee_interval` uses ecobee ids. `ecobee_daily` uses beestat ids. `config.py` maps between them (47724 ↔ 415533522899, 47723 ↔ 415522180035). 47722 has no ecobee-side data.

**Portal CSV column mapping:**

| Thermostat | Portal column | Database column |
|---|---|---|
| Living Room | Heat Stage 1 | comp_heat_s |
| Living Room | Aux Heat 1 | aux_heat_s |
| Upstairs | Heat Stage 1 | aux_heat_s |
| both | Cool Stage 1 | comp_cool_s |

Upstairs Heat Stage 1 goes to `aux_heat_s` to match how the API logs that thermostat. When both the API and a portal CSV have the same interval, the API row wins.

**`rates.csv` format:**

```
fuel,schedule,effective_date,component,tier_min,tier_max,rate_per_unit,unit
```

- `component` is one of `energy` (all-in per-unit rate as billed, per tier), `tax_adj` (Schedule 57/157 per-unit adjustment), `basic` (fixed monthly charge) or `seasonal`.
- Each row covers one tier, so tier boundaries come from the rows themselves.
- The Schedule 1 and Schedule 101 history must cover at least 2021-09-01 onward.

## 6. Sync behavior per source

| Source | What sync does |
|---|---|
| ecobee | (1) One-time import of the API backup JSON and portal CSVs. (2) Fetch from the API in 31-day chunks, back to the oldest date the API still returns. Columns include the 5-min runtime and state fields listed in §5, plus remote sensors. |
| beestat | Fetch all daily summaries in one `read_id` call, save the raw response to `data_archive/beestat/`, and upsert all rows, since beestat revises recent days. |
| Emporia | Hourly kWh for every channel on device 219614, in 30-day chunks, from 2023-04-15. |
| HA carbon | Hourly means for both statistics, from 2024-02-01. |
| Weather | Hourly from 2021-11-01 (one month before the gas data starts). |
| Local files | Re-read `rates.csv`, `exclude_dates.txt` and the gas CSVs on every sync. They are small. For `exclude_dates.txt`, all `not_home` rows with source `exclude_dates` are replaced by the file's current contents, so additions, edits and removals all take effect. |
| ecobee vacation events | Derived from `ecobee_interval` on each sync, into `not_home` with source `ecobee_vacation`. |

## 7. Daily report computation (`daily.py`)

Inputs come only from the store. `D` is the local day.

### 7.1 Per-interval runtime (5-min data available)

For each 5-minute interval, with LR = Living Room and UP = Upstairs:

- `heat_s = max(LR.comp_heat_s + LR_aux_as_hp, UP.aux_heat_s)`, where `LR_aux_as_hp = LR.aux_heat_s` before `STRIP_INSTALL_DATE` and 0 after.
- `cool_s = max(LR.comp_cool_s, UP.comp_cool_s)`
- `aux_s = LR.aux_heat_s` on or after `STRIP_INSTALL_DATE`, else 0.

Taking the max of the two zones gives the time the shared compressor was running. Day totals are the sum over the day's intervals.

5-min data is used when at least 95% of the day's intervals are present for both thermostats. Otherwise use §7.2.

### 7.2 Daily-only runtime (beestat)

- Main zone: 47724 on or after 2023-10-30, 47722 before.
- `heat = max(main.comp_heat + main.aux_as_hp, UP.aux_heat)`
- `cool = max(main.comp_cool, UP.comp_cool)`

This is a lower bound, because daily totals can't show overlap between zones. The report marks runtime as `daily_lower_bound`.

**Before 2023-04-15**, all heating runtime is reported as `furnace_runtime_h` and heat pump runtime is 0.

### 7.3 Energy

- Hourly heat pump kWh `hp_h` = `emporia_hourly` channel 7 + channel 8.
- Hourly strip kWh = (aux_s in that hour / 3600) × `STRIP_KW` (5 kW). It is 0 before `STRIP_INSTALL_DATE`.
- Each hour's `hp_h` is assigned to **heating**, **cooling** or **other** (fan-only/standby, no call):
  - With 5-min data: heating if the hour has heat_s > 0 or aux_s > 0, cooling if cool_s > 0, other if neither. If an hour has both heating and cooling, split it in proportion to runtime.
  - With daily-only data: the whole day goes to heating if day heat > 15 min, cooling if day cool > 15 min, otherwise other. No observed day has both (0 of 1,435).
- Strip kWh always goes to heating.
- Report: `heating_kwh`, `cooling_kwh`, `other_kwh`, `strip_kwh` (included in heating) and `total_kwh`.
- **Strip cross-check:** for each hour with aux_s > 0, measured extra = (mains − sum of monitored circuits) − the median of that same hour of day over the previous 14 days. If the measured extra over the day falls outside 0.5–1.5 × the strip kWh, flag `strip_crosscheck_mismatch`. The total still uses runtime × 5 kW.

### 7.4 Weather

- `hdd = Σ_hours max(0, HDD_BASE_F − T_h) / 24`
- `cdd = Σ_hours max(0, T_h − CDD_BASE_F) / 24`
- Defaults: `HDD_BASE_F = 65`, `CDD_BASE_F = 65`.
- Also report mean/min/max temperature and mean wind speed.
- Cross-check: HDD computed the same way from ecobee outdoor temperature (5-min data), or beestat's average outdoor temperature (`65 − avg`, labeled as daily-mean method).

### 7.5 Carbon

Over the hours in `D`, with total kWh per hour `k_h` (heat pump + strips):

- `gco2e = Σ k_h × co2_g_per_kwh_h`
- `low_carbon_pct = Σ k_h × (100 − fossil_pct_h) / Σ k_h`

Missing hours are filled with the mean of that day's available hours and listed in `notes`. If fewer than 12 hours are available, the result is unavailable. Before 2024-02-01 it is unavailable with reason `no grid carbon data before 2024-02`. Each category (heating, cooling, other) gets its own value.

### 7.6 Electric cost

Schedule 1 has two energy tiers (first 600 kWh per month, and above). For the rate rows in effect on `D`:

1. `M` = house mains kWh from the 1st of `D`'s calendar month up to the start of `D`.
2. `R` = mains kWh on `D` minus heat pump kWh on `D` (the rest of the house that day).
3. The day's heating+cooling+other kWh is stacked on top of `M + R`. The part below the 600 kWh tier boundary is priced at tier 1, the rest at tier 2. Each tier rate is the "total as billed" per-kWh rate.
4. Add the per-kWh tax adjustment.
5. Basic and seasonal charges are excluded, since they don't change with use.

Cost is reported in total and per category, allocated in proportion to kWh.

If `D` is after the newest rate row, use the newest rate and add a note.

### 7.7 Gas (dates with `gas_daily` rows)

- `gas_therms`
- `gas_kwh_equiv = therms × 29.3071` (energy content of the gas burned)
- `furnace_heat_kwh = gas_kwh_equiv × FURNACE_AFUE` (heat actually delivered to the house; AFUE 0.95). This is the number to compare against heat pump kWh in later analyses.
- `gas_cost = therms × (Schedule 101 per-therm total + tax adj)`, excluding the basic charge
- `gas_gco2e = therms × 5306` (EPA combustion factor 53.06 kg CO2/MMBtu)

### 7.8 Flags

- `not_home`: `D` falls in any `not_home` range.
- `heating_day`:
  - With thermostat data: heating runtime > 15 min and cooling runtime = 0.
  - Without thermostat data: `hdd ≥ HEATING_DAY_MIN_HDD` (default 5). The gas data has no summer months, so there is no gas baseline to compare against.
- `cooling_day`: same rule for cooling.

### 7.9 Output

Human-readable table by default, or `--json`. Every field is either a value or `null` plus an entry in `unavailable: {field: reason}`. Example reasons: no Emporia data before 2023-04-15, no carbon before 2024-02. `notes` lists data-source choices (5-min vs daily, forecast weather, rate fallback).

## 8. Configuration (`config.py`)

| Setting | Default | Purpose |
|---|---|---|
| `LATITUDE`, `LONGITUDE` | 46.7324, −117.0002 (Moscow, ID) | Open-Meteo location; set to the house coordinates if they differ |
| `LOCAL_TZ` | America/Los_Angeles | day boundaries |
| `HP_START_DATE` | 2023-04-15 | furnace → heat pump changeover |
| `STRIP_INSTALL_DATE` | 2025-04-01 | update when the real date is known |
| `STRIP_KW` | 5.0 | heat strip rating |
| `FURNACE_AFUE` | 0.95 | old gas furnace efficiency rating |
| `HDD_BASE_F`, `CDD_BASE_F` | 65, 65 | degree-day bases |
| `HEATING_DAY_MIN_HDD` | 5 | heating-day rule without thermostat data |
| `EMPORIA_DEVICE_GID`, `HP_CHANNELS`, `MAINS_CHANNEL` | 219614, ("7","8"), "1,2,3" | energy channels |
| `ECOBEE_API_DIR`, `ECOBEE_PORTAL_DIR`, `GAS_DIR`, `BEESTAT_DIR`, `ORIGINAL_FILES_DIR` | subfolders of `data_archive/` per §10a | source file locations |
| `EXCLUDE_DATES_FILE` | `exclude_dates.txt` in the project root | not-home date ranges |

## 9. Error handling

- Each source module chunks requests to its service's limits (§3) and retries transient network/HTTP 5xx errors with increasing waits (3 attempts).
- The ecobee token refreshes automatically. If the refresh is rejected, sync for ecobee stops with a message to run `energy ecobee-login`, and the other sources keep syncing.
- Writes are upserts in per-chunk transactions, so an interrupted sync keeps what it saved. `sync_log` lets the next run resume.
- `energy day` never raises on missing data. It reports `null` plus a reason (§7.9). It only errors on an invalid date argument or a missing database (message: run `energy sync`).

## 10. Testing

**Offline unit tests** (default `pytest` run) build a small in-memory database with hand-made data:

1. Tier split: house month-to-date use of 590 kWh plus 20 heat pump kWh prices 10 kWh at tier 1 and 10 at tier 2.
2. Carbon weighting: kWh-weighted g CO2e and low-carbon % over hours with different intensities.
3. Runtime: overlapping Living Room and Upstairs calls count once (max per interval), and pre-strip Living Room aux counts as heat pump heat.
4. Daylight saving: a 25-hour local day sums 25 hourly rows.
5. Strips: an hour with 30 min of post-install aux adds 2.5 kWh to heating kWh, cost and carbon.
6. Cooling: an hour with cooling runtime lands in cooling kWh, not heating.

**Live integration tests** (`pytest -m integration`, read-only) use a recent date, since ecobee history ages out, and check shape and plausible ranges:

- ecobee: 288 intervals per thermostat
- beestat: a summary row exists for each active thermostat
- Emporia: 24 hourly values for channels 7, 8 and mains, with 7 + 8 = 97
- HA carbon: ≥ 20 hourly values
- Open-Meteo: 24 hourly temperatures

**Acceptance:** after `energy sync`, `energy day 2026-01-15` returns non-null values for every §1 output except gas. Expected values from the 2026-10-31 exploration:

| Output | Expected |
|---|---|
| Heat pump kWh (7 + 8) | 30.84 (15.19 + 15.65) |
| Living Room compressor heat | 7.28 h, so union runtime ≥ 7.28 h |
| Aux runtime | 0 h |
| HDD (integration method) | ≥ 30.7, the daily-mean value |

Gas outputs are checked with a gas-era date such as `energy day 2022-12-15`.

## 10a. File layout and folder cleanup

**Rule:** every file that `energy sync` reads lives under `data_archive/<source>/`. Files that sync never reads go to `data_archive/original_files/`. The code never reads from `../insulation_impact_analysis`.

| Folder | Contents | Read by sync? |
|---|---|---|
| `data_archive/ecobee_api_runtime/` | ecobee API JSON responses (the 2026-10-31 backup plus every new response sync saves) | yes |
| `data_archive/ecobee_portal_csv/` | `report-*.csv` copied (not moved) from `../insulation_impact_analysis/data/` | yes |
| `data_archive/avista_gas/` | `avista-gas-daily-*.csv` and `gas_furnace_therms_history.csv`, moved from the project root | yes |
| `data_archive/beestat/` | raw beestat summary responses, one JSON per sync (a backup; sync loads from the API, not from these files) | written, not read |
| `data_archive/original_files/` | everything else from the project root: the gas `.ods` copies, Emporia export folders and zips, KPUW HDD CSVs, `heat_energy.ods`, `4BA438-Emporia_Vue-1DAY.ods`, the Avista rate PDFs (including the downloaded electric one) | no |

The project root keeps only: `.env`, `keys.json`, `.claude/`, `energy_analysis.code-workspace`, `data_archive/`, `data/` (the database), `exclude_dates.txt`, `rates.csv`, and the project's own files (`src/`, `tests/`, `docs/`, `pyproject.toml`, `uv.lock`, `.gitignore`).

Files are moved or copied, never deleted. The move to `original_files/` happens after the acceptance checks pass.

## 11. Known limitations

- **Billing cycles:** Avista bills on billing cycles, not calendar months. Tier placement uses calendar months as an approximation.
- **Gas meter reads:** they happen around 16:00, so a gas "day" is offset from the local calendar day.
- **ecobee login** uses the same unofficial web login as Home Assistant's integration, and it can break. beestat's API is labeled unstable.
- **Zone overlap:** runtime from daily-only data (Nov 2022 – Apr 2025, outside portal-CSV months) is a lower bound.
- **Weather:** Open-Meteo is modeled data, not a Moscow station.
- **No thermostat data before 2022-11-25:** runtime and vacation events are unavailable then. Not-home days come from `exclude_dates.txt`.
- **Carbon history:** grid carbon before 2024-02 is unavailable here. Hindcasting earlier carbon is a later sub-project, for example using Electricity Maps' free yearly hourly datasets.
- **Strip date and energy:** the installation date is approximate. Strip energy is runtime × rated kW, not measured.
