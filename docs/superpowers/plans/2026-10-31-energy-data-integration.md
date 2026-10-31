# Home Energy Data Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull ecobee, beestat, Emporia, Home Assistant carbon, Open-Meteo weather, Avista rates and gas history into one SQLite database, and provide `energy day YYYY-MM-DD`, which reports kWh, runtime, HDD, carbon and cost for a day.

**Architecture:** A `uv`-managed Python package `energy`. Each module in `energy/sources/` fetches or reads one source and upserts raw rows into SQLite (`energy/store.py`). Pure calculation modules in `energy/calc/` and the report assembler `energy/daily.py` read only from SQLite. `energy/cli.py` exposes `sync`, `ecobee-login` and `day`.

**Tech Stack:** Python ≥3.12 (system has 3.14), uv 0.13, stdlib `sqlite3`/`csv`/`zoneinfo`/`argparse`, `requests`, `websockets`, `pyemvue` 0.18.x, `python-ecobee-api` 0.4.4, `pytest`.

**Spec:** `docs/superpowers/specs/2026-10-31-energy-data-integration-design.md`. Read it before starting any task. Section numbers below (§N) refer to it.

## Global Constraints

- All timestamps are stored as UTC ISO strings `YYYY-MM-DDTHH:MM:SSZ`. Dates are stored as `YYYY-MM-DD`. A "day" is local midnight to midnight in `America/Los_Angeles`, so it can have 23, 24 or 25 hours.
- Secrets come only from `.env` (`HA_TOKEN`, `ECOBEE_USERNAME`, `ECOBEE_PASSWORD`, `BEESTAT_API_KEY`) and `keys.json` (`username`, `password` for Emporia). Saved tokens go in `.secrets/`. Never print or log secret values.
- The database lives at `data/energy.sqlite`. `data/`, `data_archive/`, `.secrets/`, `.env` and `keys.json` are gitignored. `rates.csv` is committed.
- Only `energy/sources/*` and `energy/cli.py` may do network or file input. `energy/calc/*` and `energy/daily.py` take a `sqlite3.Connection` or plain values.
- All writes are upserts (`INSERT OR REPLACE`), so every sync is safe to re-run.
- Heat pump kWh = Emporia channel `"7"` + channel `"8"`. Channel `"97"` is stored but used only for cross-checks.
- Constants from spec §8, verbatim:
  - `LATITUDE = 46.7324`, `LONGITUDE = -117.0002`, `LOCAL_TZ = "America/Los_Angeles"`
  - `HP_START_DATE = 2023-04-15`, `STRIP_INSTALL_DATE = 2025-04-01`, `STRIP_KW = 5.0`, `FURNACE_AFUE = 0.95`
  - `HDD_BASE_F = 65`, `CDD_BASE_F = 65`, `HEATING_DAY_MIN_HDD = 5`
  - `EMPORIA_DEVICE_GID = 219614`, `HP_CHANNELS = ("7", "8")`, `MAINS_CHANNEL = "1,2,3"`
- Thermostat ids:
  - Living Room: ecobee `"415533522899"`, beestat `47724`, beestat data starts 2023-10-30
  - Upstairs: ecobee `"415522180035"`, beestat `47723`
  - Old main thermostat: beestat `47722`, 2022-11-25 to 2023-11-01
- Gas constants: 29.3071 kWh/therm; 5306 g CO2e/therm.
- Thresholds:
  - 5-minute data is "complete" for a day at ≥ 95% of the expected intervals for both thermostats.
  - A "call" means > 15 minutes (900 s) of runtime in a day.
  - The strip cross-check ratio band is 0.5–1.5.
  - Carbon needs ≥ 12 hours of data in the day.
- Live tests are marked `@pytest.mark.integration` and excluded by default (`addopts = "-m 'not integration'"`).

## Review Focus

1. **A date with no data at all** (for example 2020-06-01, or tomorrow): `energy day` must print every field as unavailable with reasons and exit 0, not raise. Test in Task 9.
2. **The daylight-saving fall-back hour**: ecobee rows at local 01:00–01:55 on 2025-11-02 occur twice. Converting to UTC must keep both copies (24 distinct UTC timestamps for that hour range, no primary-key collision). Test in Task 7.
3. **API rows older than ecobee's retention window**: they come back with blank modes and zero runtime. They must never overwrite good portal-CSV or backup rows. Test in Task 7.
4. **A revoked ecobee refresh token**: `energy sync` must report the ecobee failure with "run `energy ecobee-login`" and still sync every other source. Test in Task 9.
5. **Rate lookup outside the table**: a date after the newest rate row uses the newest rate and adds a note. A date before the oldest row makes cost unavailable. Test in Task 2.

---

### Task 1: Project scaffold, config and store

**Files:**
- Create: `pyproject.toml`, `src/energy/__init__.py`, `src/energy/config.py`, `src/energy/store.py`, `src/energy/http.py`, `src/energy/sources/__init__.py`, `src/energy/calc/__init__.py`, `tests/conftest.py`, `tests/test_store.py`

**Interfaces:**
- Produces:
  - `energy.config`: every constant listed in Global Constraints, plus:
    - Paths: `PROJECT_ROOT: Path`, `DB_PATH`, `SECRETS_DIR`, `ARCHIVE_DIR = PROJECT_ROOT/"data_archive"`, `INSULATION_DIR = PROJECT_ROOT.parent/"insulation_impact_analysis"`, `RATES_CSV = PROJECT_ROOT/"rates.csv"`
    - Thermostat ids: `LR_ECOBEE_ID`, `UP_ECOBEE_ID`, `BEESTAT_LR = 47724`, `BEESTAT_UP = 47723`, `BEESTAT_OLD = 47722`, `LR_BEESTAT_START = date(2023,10,30)`
    - `VIRTUAL_CHANNELS = ("97", "98")`
    - `EXCLUDE_DATES_FILE = PROJECT_ROOT/"exclude_dates.txt"`
    - `ORIGINAL_FILES_DIR = ARCHIVE_DIR/"original_files"`, and `GAS_FILES_DIR = PROJECT_ROOT` (Task 10 changes it to `ORIGINAL_FILES_DIR`)
    - `load_secrets() -> dict[str, str]`: merges `.env` key/values with `EMPORIA_USERNAME`/`EMPORIA_PASSWORD` from `keys.json`.
  - `energy.store`:
    - `connect(path: str | Path = DB_PATH) -> sqlite3.Connection`: creates every table in spec §5 if missing (`":memory:"` allowed), sets `row_factory = sqlite3.Row`, creates parent dirs.
    - `upsert(conn, table: str, rows: Iterable[dict]) -> int`: `INSERT OR REPLACE` keyed by the table's primary key; returns the row count.
    - `log_sync(conn, source: str, range_start: str, range_end: str, rows: int, status: str) -> None`
    - `local_day_bounds(day: date) -> tuple[str, str]`: UTC ISO start (inclusive) and end (exclusive) of the local day.
  - `energy.http.request(method: str, url: str, *, attempts: int = 3, **kwargs) -> requests.Response`: retries on `requests.ConnectionError`, `requests.Timeout` and HTTP 5xx, waiting 2 s then 4 s, and raises after the last attempt. 4xx responses are returned without retry. All HTTP in `sources/*` goes through it, with a default timeout of 120 s.
- `tests/conftest.py` fixture `db` yields `store.connect(":memory:")`.

- [ ] **Step 1: Write `pyproject.toml`**

Project `energy`, src layout, dependencies `requests`, `websockets`, `pyemvue`, `python-ecobee-api==0.4.4`. Dev dependency `pytest`. Script `energy = "energy.cli:main"`. Pytest config: `markers = ["integration: hits live APIs"]`, `addopts = "-m 'not integration'"`. Then run `uv sync`. Expected: creates `.venv` with no errors.

- [ ] **Step 2: Write the failing tests** in `tests/test_store.py`

```python
def test_schema_has_all_tables(db):
    names = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    assert {"ecobee_interval","ecobee_sensor","ecobee_daily","emporia_hourly","grid_carbon_hourly",
            "weather_hourly","gas_daily","rates","not_home","sync_log"} <= names

def test_upsert_is_idempotent(db):
    row = {"channel": "7", "ts": "2026-01-15T08:00:00Z", "kwh": 1.5, "channel_name": "heatpump"}
    assert store.upsert(db, "emporia_hourly", [row]) == 1
    store.upsert(db, "emporia_hourly", [{**row, "kwh": 2.0}])
    assert db.execute("select count(*), max(kwh) from emporia_hourly").fetchone() == (1, 2.0)

def test_local_day_bounds_dst():
    assert store.local_day_bounds(date(2025, 11, 2)) == ("2025-11-02T07:00:00Z", "2025-11-03T08:00:00Z")
    assert store.local_day_bounds(date(2026, 1, 15)) == ("2026-01-15T08:00:00Z", "2026-01-16T08:00:00Z")

def test_http_retries_5xx_then_returns(monkeypatch):
    responses = [FakeResp(503), FakeResp(200)]
    monkeypatch.setattr(requests, "request", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    assert http.request("GET", "https://x").status_code == 200 and responses == []
```

`FakeResp(status)` is a tiny class in the test module with `status_code` and `raise_for_status()`.

- [ ] **Step 3: Run the tests to confirm they fail.** Run: `uv run pytest tests/test_store.py -v`. Expected: FAIL (module not found).

- [ ] **Step 4: Implement `config.py` and `store.py`** with the columns from spec §5. Every key column is `NOT NULL`, and each table declares `PRIMARY KEY(...)` on its key columns.

- [ ] **Step 5: Run the tests to confirm they pass.** Run: `uv run pytest -v`. Expected: 4 passed.

- [ ] **Step 6: Commit**: `git add pyproject.toml uv.lock src tests && git commit -m "feat: project scaffold, config and SQLite store"`

---

### Task 2: Local files: rates, not-home dates, gas

**Files:**
- Create: `rates.csv`, `src/energy/sources/local_files.py`, `src/energy/calc/rates.py`, `tests/test_local_files.py`, `tests/test_rates.py`

**Interfaces:**
- Consumes: `store.connect`, `store.upsert`, `config.RATES_CSV`, `config.INSULATION_DIR`
- Produces:
  - `local_files.parse_exclude_dates(text: str) -> list[tuple[date, date]]`: handles `YYYY-M-D -- YYYY-M-D` ranges, single dates, and `#` comments.
  - `local_files.sync(conn) -> int`: loads `rates.csv` → `rates`; `config.EXCLUDE_DATES_FILE` → `not_home` (deletes all rows with source `exclude_dates`, then inserts the file's current ranges, in one transaction); `gas_furnace_therms_history.csv` (columns `Date,Therms,HDD – 65F,In Town`) → `gas_daily.therms`/`in_town`; raw `avista-gas-daily-*.csv` CCF values → `gas_daily.ccf`, keyed by the local date of each reading.
  - `calc.rates.rate_on(conn, fuel: str, schedule: str, day: date) -> RateSet | None`
  - `RateSet` is a dataclass with:
    - `effective_date: date`
    - `tiers: list[tuple[float, float | None, float]]`, one `(tier_min, tier_max, rate_per_unit)` per tier, sorted
    - `tax_adj: float` (0 if no row)
    - `basic: float`
    - `is_fallback: bool`: True when `day` is after a year past the newest effective date. Then the newest rate is used and the report adds a note.
  - Returns `None` when `day` is before the oldest row.

- [ ] **Step 1: Transcribe `rates.csv`.** Download `https://www.myavista.com/-/media/myavista/content-documents/our-rates-and-tariffs/id/ideratehist9126.pdf` into `data/`, then run `pdftotext -layout`. Do the same for the existing `IDGRatehist9126.pdf`. Write one row per effective date, component and tier for:
  - electric Schedule 1, every change from the one in effect on 2021-09-01 onward
  - gas Schedule 101, from 09/01/21 onward

  Columns: `fuel,schedule,effective_date,component,tier_min,tier_max,rate_per_unit,unit`.
  - `fuel`: `electric` or `gas`
  - `component`: `energy`, `tax_adj`, `basic` or `seasonal`
  - `energy` rows use the "Total As Billed" column
  - Schedule 1 tiers: `0,600` and `600,` (empty max)
  - gas has a single tier `0,`

  Check against these values from the PDFs:
  - electric 2025-10-01: tier 1 0.09641, tier 2 0.10863, basic 20.00
  - electric 2026-09-01: tier 1 0.10598, tier 2 0.11886, tax_adj 0.000923
  - gas 2021-11-01: 0.72905, basic 6.00
  - gas 2025-11-01: 0.58428, basic 20.00
  - gas 2026-01-01: tax_adj 0.010802

- [ ] **Step 2: Write the failing tests**

```python
def test_parse_exclude_dates():
    txt = "# c\n2021-12-23 -- 2022-1-17\n2025-8-16 -- 2025-8-17\n"
    assert parse_exclude_dates(txt) == [(date(2021,12,23), date(2022,1,17)), (date(2025,8,16), date(2025,8,17))]

def test_exclude_dates_resync_adds_and_removes(db, tmp_path, monkeypatch):
    f = tmp_path / "exclude_dates.txt"; monkeypatch.setattr(config, "EXCLUDE_DATES_FILE", f)
    f.write_text("2025-8-16 -- 2025-8-17\n"); local_files.sync(db)
    f.write_text("2026-11-20 -- 2026-11-27\n"); local_files.sync(db)
    assert db.execute("select start_date, end_date from not_home where source='exclude_dates'").fetchall() == [("2026-11-20", "2026-11-27")]

def test_rate_on_known_dates(db):
    local_files.sync(db)
    r = rate_on(db, "electric", "1", date(2025, 12, 15))
    assert r.tiers == [(0, 600, 0.09641), (600, None, 0.10863)] and r.basic == 20.0
    g = rate_on(db, "gas", "101", date(2022, 12, 15))
    assert g.tiers[0][2] == 0.72905

def test_rate_outside_table(db):          # Review Focus 5
    local_files.sync(db)
    assert rate_on(db, "electric", "1", date(2015, 1, 1)) is None
    assert rate_on(db, "electric", "1", date(2030, 1, 1)).is_fallback is True
```

- [ ] **Step 3: Run the tests to confirm they fail**: `uv run pytest tests/test_local_files.py tests/test_rates.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement `local_files.py` and `calc/rates.py`.** Gas CSVs are read from `config.GAS_FILES_DIR`, looked up at call time, never copied into a module-level constant.
- [ ] **Step 5: Run the tests to confirm they pass.** Expected: all pass.
- [ ] **Step 6: Commit**: `git add rates.csv src tests && git commit -m "feat: local files loader and Avista rate lookup"`

---

### Task 3: Emporia source

**Files:**
- Create: `src/energy/sources/emporia.py`, `tests/test_emporia.py`

**Interfaces:**
- Consumes: `store.upsert`, `store.log_sync`, `config.load_secrets`, `config.EMPORIA_DEVICE_GID`, `config.SECRETS_DIR`
- Produces:
  - `emporia.rows_from_chart(channel: str, name: str, usage: list[float | None], first: datetime, end: datetime) -> list[dict]`: one `emporia_hourly` row per non-None value with timestamp `first + i h` < `end`.
  - `emporia.sync(conn, since: date | None = None) -> int`

- [ ] **Step 1: Write the failing test**

```python
def test_rows_from_chart_drops_none_and_trailing():
    first = datetime(2026,1,15,8,tzinfo=UTC); end = first + timedelta(hours=2)
    rows = rows_from_chart("7", "heatpump", [1.0, None, 3.0], first, end)
    assert [(r["ts"], r["kwh"]) for r in rows] == [("2026-01-15T08:00:00Z", 1.0)]
```

- [ ] **Step 2: Run it to confirm it fails.**
- [ ] **Step 3: Implement.** Log in with `PyEmVue().login(username, password, token_storage_file=SECRETS_DIR/"emporia_tokens.json")`. Collect channels from every device with `device_gid == 219614`; channel `"1,2,3"` is mains, named `mains`. For each channel, call `get_chart_usage(ch, start, end, scale=Scale.HOUR.value, unit=Unit.KWH.value)` in 30-day chunks. Start from `since`, else the day after the latest stored ts minus 2 days, else `2023-04-15`. Log each chunk.
- [ ] **Step 4: Run it to confirm it passes.**
- [ ] **Step 5: Commit**: `git commit -am "feat: Emporia hourly sync"` (after `git add` of the new files).

---

### Task 4: Home Assistant carbon source

**Files:**
- Create: `src/energy/sources/ha_carbon.py`, `tests/test_ha_carbon.py`

**Interfaces:**
- Produces:
  - `ha_carbon.parse_statistics(result: dict) -> list[dict]`: input is the `result` of `recorder/statistics_during_period`. Output is `grid_carbon_hourly` rows (`ts` from the `start` epoch-ms, `co2_g_per_kwh` from `sensor.electricity_maps_co2_intensity` mean, `fossil_pct` from `sensor.electricity_maps_grid_fossil_fuel_percentage` mean). Hours where either value is missing get `None` for that value.
  - `ha_carbon.sync(conn, since: date | None = None) -> int`

- [ ] **Step 1: Write the failing test**

```python
def test_parse_statistics_joins_both_ids():
    res = {"sensor.electricity_maps_co2_intensity": [{"start": 1768464000000, "mean": 234.0}],
           "sensor.electricity_maps_grid_fossil_fuel_percentage": [{"start": 1768464000000, "mean": 37.5}]}
    assert parse_statistics(res) == [{"ts": "2026-01-15T08:00:00Z", "co2_g_per_kwh": 234.0, "fossil_pct": 37.5}]
```

- [ ] **Step 2: Run it to confirm it fails.**
- [ ] **Step 3: Implement.** Connect to `ws://192.168.50.206:8123/api/websocket` (`max_size=None`) and authenticate with `HA_TOKEN`. Request `period="hour"`, `types=["mean"]`, in 90-day chunks from `since`, else latest stored minus 2 days, else `2024-02-01`. Wrap the call in `asyncio.run`.
- [ ] **Step 4: Run it to confirm it passes.**
- [ ] **Step 5: Commit.**

---

### Task 5: Open-Meteo weather source

**Files:**
- Create: `src/energy/sources/weather.py`, `tests/test_weather.py`

**Interfaces:**
- Produces:
  - `weather.parse_open_meteo(payload: dict, source: str) -> list[dict]`: `weather_hourly` rows from `payload["hourly"]["time"|"temperature_2m"|"wind_speed_10m"]`. Times are UTC `YYYY-MM-DDTHH:MM`. Hours with a null temperature are skipped.
  - `weather.sync(conn, since: date | None = None) -> int`

- [ ] **Step 1: Write the failing test**

```python
def test_parse_open_meteo():
    p = {"hourly": {"time": ["2026-01-15T08:00", "2026-01-15T09:00"], "temperature_2m": [30.2, None],
                    "wind_speed_10m": [4.3, 3.5]}}
    assert parse_open_meteo(p, "archive") == [{"ts": "2026-01-15T08:00:00Z", "temp_f": 30.2, "wind_mph": 4.3, "source": "archive"}]
```

- [ ] **Step 2: Run it to confirm it fails.**
- [ ] **Step 3: Implement.** Use the archive API with `latitude`/`longitude` from config, `hourly=temperature_2m,wind_speed_10m`, `temperature_unit=fahrenheit`, `wind_speed_unit=mph`, `timezone=UTC`, in 1-year chunks from `since`, else latest archive row minus 7 days, else `2021-11-01`. Then call the forecast API (`api.open-meteo.com/v1/forecast`, `past_days=10`, `forecast_days=1`, same params). Only insert forecast hours that have no archive row; archive rows always overwrite forecast rows.
- [ ] **Step 4: Run it to confirm it passes.**
- [ ] **Step 5: Commit.**

---

### Task 6: beestat daily summaries

**Files:**
- Create: `src/energy/sources/beestat.py`, `tests/test_beestat.py`

**Interfaces:**
- Produces:
  - `beestat.parse_summaries(rows: list[dict]) -> list[dict]`: maps beestat fields to `ecobee_daily` rows. Fields map as follows:

    | beestat field | `ecobee_daily` column |
    |---|---|
    | `sum_compressor_heat_1` | `comp_heat_s` |
    | `sum_compressor_cool_1` | `comp_cool_s` |
    | `sum_auxiliary_heat_1` | `aux_heat_s` |
    | `sum_fan` | `fan_s` |
    | `avg/min/max_outdoor_temperature` | `avg/min/max_outdoor_f` |
    | `avg_indoor_temperature` | `avg_indoor_f` |
    | `count` | `interval_count` |

    `thermostat_id` stays the beestat int id, `source` is `"beestat"`, and rows with `deleted` true are skipped.
  - `beestat.sync(conn, since: date | None = None) -> int`: one call to `https://api.beestat.io/?api_key=…&resource=runtime_thermostat_summary&method=read_id`, upserting every row.

- [ ] **Step 1: Write the failing test** using one row copied from `data_archive/beestat/runtime_thermostat_summary_2026-10-31.json` (thermostat 47722, date 2022-11-25). Assert `aux_heat_s == 7140`, `fan_s == 7770` and `avg_outdoor_f == 39.6`.
- [ ] **Step 2: Run it to confirm it fails.**
- [ ] **Step 3: Implement.** `data` may be a dict keyed by id or a list; handle both. Set a `User-Agent` header.
- [ ] **Step 4: Run it to confirm it passes.**
- [ ] **Step 5: Commit.**

---

### Task 7: ecobee source (login, API, backup, portal CSVs, vacations)

**Files:**
- Create: `src/energy/sources/ecobee.py`, `tests/test_ecobee.py`, `tests/fixtures/portal_sample.csv` (header block plus 3 rows copied from a real portal export)

**Interfaces:**
- Produces:
  - `ecobee.ECOBEE_TS_IS_UTC: bool`, determined in Step 1.
  - `ecobee.parse_runtime_report(payload: dict) -> tuple[list[dict], list[dict]]`: `ecobee_interval` rows (source `"api"`) and `ecobee_sensor` rows. Column mapping:

    | API column | `ecobee_interval` column |
    |---|---|
    | `compHeat1` | `comp_heat_s` |
    | `compCool1` | `comp_cool_s` |
    | `auxHeat1` | `aux_heat_s` |
    | `fan` | `fan_s` |
    | `outdoorTemp` | `outdoor_temp_f` |
    | `zoneAveTemp` | `indoor_temp_f` |
    | `zoneHeatTemp`, `zoneCoolTemp` | setpoints |
    | `hvacMode` | `hvac_mode` |
    | `zoneHvacMode` | `zone_hvac_mode` |
    | `zoneCalendarEvent` | `calendar_event` |
    | `zoneClimate` | `climate` |

    **Rows where both `hvacMode` and `zoneHvacMode` are empty are dropped.** Those are pre-retention filler rows.
  - `ecobee.parse_portal_csv(path: Path) -> list[dict]`: reads with the first 5 lines skipped, `csv.DictReader`, and the trailing empty field ignored. The thermostat id comes from the `#,Thermostat,identifier,` line. Mapping per spec §5: Upstairs `Heat Stage 1 (sec)` → `aux_heat_s`; Living Room `Heat Stage 1 (sec)` → `comp_heat_s` and `Aux Heat 1 (sec)` → `aux_heat_s`. Source is `"portal_csv"`. Rows with an empty `System Mode` are dropped.
  - `ecobee.to_utc(d: str, t: str, fold: int) -> str`: if `ECOBEE_TS_IS_UTC`, pass through. Otherwise localize with `LOCAL_TZ`; `fold` is 1 for the second occurrence of a repeated local time within one thermostat's row sequence.
  - `ecobee.derive_vacations(conn) -> int`: collects intervals whose `calendar_event`, with `(SmartRecovery)` stripped, matches `^\d{9,}$`, `^[0-9A-F]{8,}$` or starts with `Vacation`. Groups them by event name into the first and last local date. Upserts into `not_home` with source `"ecobee_vacation"`.
  - `ecobee.login_interactive() -> None`: `pyecobee.Ecobee(config={"API_KEY": None})`, sets username/password, calls `request_tokens_web()`. On `EcobeeAuthMfaRequiredError`, prompts for the code via `input()` and calls `submit_mfa_code`. Saves `{"access_token", "refresh_token"}` to `SECRETS_DIR/"ecobee_tokens.json"` with mode 600.
  - `ecobee.access_token() -> str`: POST to `https://auth.ecobee.com/oauth/token` with `grant_type=refresh_token` and `client_id=183eORFPlXyz9BbDZwqexHPBQoVjgadh`, saving any rotated refresh token. A missing token file or HTTP 4xx raises `EcobeeLoginRequired("run `energy ecobee-login`")`.
  - `ecobee.sync(conn, since: date | None = None) -> int`, in this order:
    1. import every portal CSV in `INSULATION_DIR/"data"`
    2. import every JSON file in `ARCHIVE_DIR/"ecobee_api_runtime"`
    3. call the API in 31-day chunks, from `since`, else the latest API ts minus 2 days, else today minus 600 days, through today. Request columns `auxHeat1,compCool1,compHeat1,fan,outdoorTemp,zoneAveTemp,zoneHeatTemp,zoneCoolTemp,hvacMode,zoneHvacMode,zoneCalendarEvent,zoneClimate` with `includeSensors: true`.
    4. call `derive_vacations`.

    Each new API response is also written to `ARCHIVE_DIR/"ecobee_api_runtime"` as `runtimeReport_<start>_<end>.json`.

- [ ] **Step 1: Determine the ecobee timestamp basis.** Write a throwaway script, not committed, that loads one January backup file and `emporia_hourly` (run Task 3's sync first). Correlate the hourly sums of Living Room `compHeat1` with hourly channel 7 + 8 kWh, with ecobee times read as (a) UTC and (b) `America/Los_Angeles`. Set `ECOBEE_TS_IS_UTC` to the reading with the higher correlation, and record both correlation values in a code comment next to it. The sensor block in the backup starts at the previous day's 17:00, so check sensor timestamps the same way. If they differ from the runtime rows, add a separate `ECOBEE_SENSOR_TS_IS_UTC`.

- [ ] **Step 2: Write the failing tests**

```python
def test_parse_runtime_report_drops_pre_retention_rows():
    payload = {"columns": "auxHeat1,compCool1,compHeat1,fan,outdoorTemp,zoneAveTemp,zoneHeatTemp,zoneCoolTemp,hvacMode,zoneHvacMode,zoneCalendarEvent,zoneClimate",
               "reportList": [{"thermostatIdentifier": "415533522899", "rowList": [
                   "2024-12-15,00:00:00,0,0,0,0,30.1,,,,,,,",
                   "2026-01-15,00:05:00,0,0,300,300,32.6,65,66,,heat,compressorHeatStage1On,,Sleep"]}],
               "sensorList": []}
    intervals, _ = parse_runtime_report(payload)
    assert len(intervals) == 1 and intervals[0]["comp_heat_s"] == 300

def test_api_rows_do_not_clobber_portal_rows(db):            # Review Focus 3
    store.upsert(db, "ecobee_interval", parse_portal_csv(FIXTURES / "portal_sample.csv"))
    before = db.execute("select count(*) from ecobee_interval where source='portal_csv'").fetchone()[0]
    store.upsert(db, "ecobee_interval", parse_runtime_report(PRE_RETENTION_PAYLOAD_FOR_SAME_TIMES)[0])
    assert db.execute("select count(*) from ecobee_interval where source='portal_csv'").fetchone()[0] == before

def test_fall_back_hour_keeps_both_copies():                  # Review Focus 2 (skip if ECOBEE_TS_IS_UTC)
    rows = [f"2025-11-02,01:{m:02d}:00,0,0,0,0,40,65,66,,heat,heatOff,,Sleep" for m in range(0, 60, 5)] * 2
    intervals, _ = parse_runtime_report(payload_with(rows))
    assert len({r["ts"] for r in intervals}) == 24

def test_portal_csv_upstairs_heat_goes_to_aux():
    rows = parse_portal_csv(FIXTURES / "portal_sample.csv")   # Upstairs sample, a row with Heat Stage 1 = 195
    assert any(r["aux_heat_s"] == 195 and r["comp_heat_s"] == 0 for r in rows)

def test_derive_vacations(db):
    store.upsert(db, "ecobee_interval", vacation_rows("176645687997", "2025-12-22", "2025-12-30"))
    ecobee.derive_vacations(db)
    assert db.execute("select start_date, end_date from not_home where source='ecobee_vacation'").fetchall() == [("2025-12-22", "2025-12-30")]
```

`PRE_RETENTION_PAYLOAD_FOR_SAME_TIMES`, `payload_with` and `vacation_rows` are small helpers in the test module that build payloads and rows in the formats above.

- [ ] **Step 3: Run the tests to confirm they fail.**
- [ ] **Step 4: Implement `ecobee.py`.** If `.secrets/ecobee_tokens.json` is missing and a valid `ecobee_tokens.json` exists in the brainstorming scratchpad, copy it rather than asking the user for a new code. Otherwise ask the user to run `energy ecobee-login` once Task 9 exists. For this task, `uv run python -c "from energy.sources import ecobee; ecobee.login_interactive()"` works.
- [ ] **Step 5: Run the tests to confirm they pass.**
- [ ] **Step 6: Commit.**

---

### Task 8: Calculation modules

**Files:**
- Create: `src/energy/calc/runtime.py`, `src/energy/calc/energy.py`, `src/energy/calc/weather.py`, `src/energy/calc/carbon.py`, `src/energy/calc/cost.py`, `tests/test_calc.py`

**Interfaces:**
- Consumes: `store.local_day_bounds`, `calc.rates.rate_on`, config constants
- Produces:
  - `runtime.day_runtime(conn, day: date) -> Runtime`. `Runtime` is a dataclass with:
    - `basis: str | None`: `"5min"`, `"daily_lower_bound"` or None
    - `heat_h`, `cool_h`, `aux_h`, `furnace_h: float | None`
    - `zones: dict[str, dict[str, float]]`, keyed `"LR"`/`"UP"`/`"OLD"` → `{"heat","cool","aux"}` hours
    - `hourly: dict[str, dict[str, float]]`: UTC hour ts → `{"heat_s","cool_s","aux_s"}`, empty for daily basis

    Rules from spec §7.1–§7.2. Living Room aux counts as heat before `STRIP_INSTALL_DATE`. Before `HP_START_DATE`, heat goes to `furnace_h` and `heat_h = 0`.
  - `energy.day_energy(conn, day: date, rt: Runtime) -> Energy`. `Energy` is a dataclass with:
    - `heating_kwh`, `cooling_kwh`, `other_kwh`, `strip_kwh`, `total_kwh: float | None`
    - `hourly_kwh: dict[str, float]` (heat pump + strip per UTC hour)
    - `hourly_category: dict[str, dict[str, float]]`
    - `strip_crosscheck_mismatch: bool`
    - `house_kwh: float | None` (mains for the day)
    - `month_to_date_house_kwh: float | None`

    Rules from spec §7.3. kWh is None if any hour of the day lacks channel 7 or 8.
  - `weather.degree_days(temps_f: list[float], base: float, kind: str) -> float`, with `kind` `"heat"` or `"cool"`: Σ max(0, base − T)/24, or the cooling equivalent.
  - `weather.day_weather(conn, day: date) -> dict` with keys `hdd, cdd, temp_mean_f, temp_min_f, temp_max_f, wind_mean_mph, hdd_ecobee, hdd_ecobee_method, source`.
  - `carbon.day_carbon(conn, day: date, hourly_kwh: dict[str, float], hourly_category) -> dict` with keys `gco2e`, `gco2e_by_category`, `low_carbon_pct`, `filled_hours`, or `None` plus a reason (spec §7.5).
  - `cost.electric_cost(rates: RateSet, prior_house_kwh: float, rest_of_house_kwh: float, kwh_by_category: dict[str, float]) -> dict` with keys `cost_usd`, `cost_by_category` (spec §7.6).
  - `cost.gas_day(conn, day: date) -> dict | None` with keys `gas_therms, gas_kwh_equiv, furnace_heat_kwh, gas_cost_usd, gas_gco2e` (spec §7.7).

- [ ] **Step 1: Write the failing tests.** These are spec §10 tests 1–6, plus the HDD method.

```python
def test_tier_split():
    rs = RateSet(date(2025,10,1), [(0,600,0.10),(600,None,0.20)], tax_adj=0.0, basic=20.0, is_fallback=False)
    out = electric_cost(rs, prior_house_kwh=590, rest_of_house_kwh=0, kwh_by_category={"heating": 20})
    assert out["cost_usd"] == pytest.approx(10*0.10 + 10*0.20)

def test_carbon_weighting(db):
    # 24 carbon hours on D: H1 = 100 g / 30% fossil, H2 = 300 g / 50% fossil, other 22 hours = 200 g / 40%
    # kWh: H1 = 1.0, H2 = 3.0, all other hours 0
    c = day_carbon(db, D, {H1: 1.0, H2: 3.0}, {H1: {"heating": 1.0}, H2: {"heating": 3.0}})
    assert c["gco2e"] == pytest.approx(1*100 + 3*300)
    assert c["low_carbon_pct"] == pytest.approx((1*70 + 3*50) / 4)

def test_carbon_unavailable_with_few_hours(db):
    # only 11 carbon hours stored for D
    assert day_carbon(db, D, {H1: 1.0}, {H1: {"heating": 1.0}})["gco2e"] is None

def test_runtime_union_and_preinstall_aux(db):   # LR comp 300 s + UP heat 300 s in the same interval → 300 s; LR aux 2025-01 → heat
    ...insert full-day 5-min rows for both thermostats...
    rt = day_runtime(db, date(2025, 1, 15))
    assert rt.basis == "5min" and rt.heat_h == pytest.approx(expected) and rt.aux_h == 0

def test_dst_day_has_25_hours(db):
    ...insert 25 hourly rows for channels 7/8 across local 2025-11-02...
    assert day_energy(db, date(2025, 11, 2), rt).total_kwh == pytest.approx(25 * 2.0)

def test_strip_kwh(db):                 # 30 min of LR aux in one hour after STRIP_INSTALL_DATE
    assert e.strip_kwh == pytest.approx(2.5) and e.heating_kwh == pytest.approx(hp_heating + 2.5)

def test_cooling_hour_goes_to_cooling(db):
    assert e.cooling_kwh == pytest.approx(cooling_hour_kwh) and e.heating_kwh == 0

def test_hdd_integration_method():
    temps = [50]*12 + [75]*12
    assert degree_days(temps, 65, "heat") == pytest.approx(7.5)   # daily-mean method would give 2.5
```

Write the `...` setup with small helpers in `tests/test_calc.py`. Each test inserts only the rows it needs.

- [ ] **Step 2: Run the tests to confirm they fail.**
- [ ] **Step 3: Implement the five modules.** In `day_runtime`, the daily fallback reads `ecobee_daily`: the main zone is `BEESTAT_OLD` before `LR_BEESTAT_START`, `BEESTAT_LR` from then on, and Upstairs is `BEESTAT_UP`. The strip cross-check (spec §7.3) uses mains minus the sum of channels whose names are numeric and not in `VIRTUAL_CHANNELS`. `hdd_ecobee` uses 5-min outdoor temps with the integration method, labeled `"hourly"`, else beestat `avg_outdoor_f` with `"daily_mean"`.
- [ ] **Step 4: Run the tests to confirm they pass**: `uv run pytest -v`. Expected: all pass.
- [ ] **Step 5: Commit.**

---

### Task 9: Daily report, CLI, live tests, acceptance

**Files:**
- Create: `src/energy/daily.py`, `src/energy/cli.py`, `tests/test_daily.py`, `tests/test_cli.py`, `tests/test_integration.py`

**Interfaces:**
- Consumes: all `calc.*` functions, every `sources.*.sync`, `ecobee.login_interactive`, `ecobee.EcobeeLoginRequired`
- Produces:
  - `daily.compute_day(conn, day: date) -> dict`. Keys: `date`, `not_home`, `heating_day`, `cooling_day`, every `Energy` and `Runtime` value, every `day_weather` key, carbon keys, `cost_usd`, `cost_by_category`, gas keys, `flags: list[str]`, `unavailable: dict[str, str]`, `notes: list[str]`. Missing inputs give `None` plus an `unavailable` entry. It never raises for a valid date.
  - `daily.format_table(report: dict) -> str`
  - `cli.SOURCES: dict[str, ModuleType]`: ordered `{"local": local_files, "emporia": emporia, "ha_carbon": ha_carbon, "weather": weather, "beestat": beestat, "ecobee": ecobee}`. `sync` looks up `SOURCES[name].sync` at call time, so tests can monkeypatch it.
  - `cli.main(argv: list[str] | None = None) -> int` with subcommands:
    - `sync [SOURCE ...] [--since DATE]`: sources `local, emporia, ha_carbon, weather, beestat, ecobee`, in that order. Each source is wrapped so that any failure prints `"<source>: FAILED: <message>"` and the next source still runs. Exit code 1 if any source failed.
    - `ecobee-login`
    - `day DATE [--json]`

- [ ] **Step 1: Write the failing tests**

```python
def test_day_with_no_data_reports_unavailable(db):             # Review Focus 1
    r = compute_day(db, date(2020, 6, 1))
    assert r["total_kwh"] is None and "total_kwh" in r["unavailable"] and r["gco2e"] is None

def test_heating_day_flag(db):
    ...LR heat 2 h, no cooling...
    assert compute_day(db, D)["heating_day"] is True

def test_sync_continues_after_ecobee_login_failure(monkeypatch, capsys):   # Review Focus 4
    monkeypatch.setattr(ecobee, "sync", raise_(EcobeeLoginRequired("run `energy ecobee-login`")))
    calls = []
    for name in ["local", "emporia", "ha_carbon", "weather", "beestat"]:
        monkeypatch.setattr(SOURCES[name], "sync", lambda conn, since=None, n=name: calls.append(n) or 0)
    assert cli.main(["sync"]) == 1
    out = capsys.readouterr().out
    assert "ecobee: FAILED" in out and "energy ecobee-login" in out
    assert calls == ["local", "emporia", "ha_carbon", "weather", "beestat"]
```

`tests/test_integration.py` has the five live tests from spec §10, all `@pytest.mark.integration`, using `date.today() - timedelta(days=3)`:
- ecobee: `parse_runtime_report` returns 288 intervals per thermostat
- beestat: a summary row exists for 47723 and 47724
- Emporia: 24 hourly values for `"7"`, `"8"` and `"1,2,3"`, with `"7"` + `"8"` ≈ `"97"`
- HA: ≥ 20 carbon hours
- Open-Meteo: 24 hourly temperatures

- [ ] **Step 2: Run the tests to confirm they fail.**
- [ ] **Step 3: Implement `daily.py` and `cli.py`.** The `day` subcommand exits 0 with the message `database not found — run "energy sync"` when `DB_PATH` is missing. The table groups fields as Energy, Runtime, Weather, Carbon, Cost, Gas and Flags, and prints `unavailable` reasons inline.
- [ ] **Step 4: Run the offline tests**: `uv run pytest -v`. Expected: all pass.
- [ ] **Step 5: Run the live tests**: `uv run pytest -m integration -v`. Expected: 5 passed. If ecobee fails with a login error, ask the user to run `uv run energy ecobee-login`.
- [ ] **Step 6: Full sync**: `uv run energy sync`. Expected: every source prints a row count and the exit code is 0.
- [ ] **Step 7: Acceptance**: `uv run energy day 2026-01-15 --json`. Expected:
  - `total_kwh` ≈ 30.84
  - zone Living Room heat ≈ 7.28 h, `heat_h` ≥ 7.28
  - `aux_h` == 0
  - `hdd` ≥ 30.7
  - `gco2e`, `low_carbon_pct` and `cost_usd` non-null
  - `unavailable` contains only gas keys

  Then run `uv run energy day 2022-12-15`. Expected: gas therms, `furnace_heat_kwh` = therms × 29.3071 × 0.95, and gas cost at 1.03788 $/therm (the 11/01/22 rate), with kWh and carbon unavailable and the reasons shown. If any value is off, fix the cause before claiming completion.
- [ ] **Step 8: Commit**: `git add src tests && git commit -m "feat: daily report and energy CLI"`

---

### Task 10: Archive the original files (spec §10a)

**Files:**
- Modify: `src/energy/config.py` (`GAS_FILES_DIR = ORIGINAL_FILES_DIR`)
- Test: `tests/test_local_files.py`

**Interfaces:**
- Consumes: `config.GAS_FILES_DIR`, `local_files.sync`, `cli.main`

Do this task only after Task 9's acceptance step passes.

- [ ] **Step 1: Write the failing test**

```python
def test_gas_files_read_from_configured_dir(db, tmp_path, monkeypatch):
    (tmp_path / "gas_furnace_therms_history.csv").write_text("Date,Therms,HDD – 65F,In Town\n2022-12-15,3.5,40,1\n")
    monkeypatch.setattr(config, "GAS_FILES_DIR", tmp_path)
    local_files.sync(db)
    assert db.execute("select therms from gas_daily where date='2022-12-15'").fetchone()[0] == 3.5

def test_gas_dir_is_archive():
    assert config.GAS_FILES_DIR == config.ORIGINAL_FILES_DIR
```

- [ ] **Step 2: Run them.** Expected: the first passes (Task 2 already reads from `GAS_FILES_DIR`), the second fails.
- [ ] **Step 3: Record the "before" state**: `uv run energy day 2022-12-15 --json > data/before_gas.json` and `sqlite3 data/energy.sqlite "select count(*), sum(therms) from gas_daily"`.
- [ ] **Step 4: Move the files** with `mkdir -p data_archive/original_files` and `mv`. Move exactly these, keeping names and folders:
  - the 4 `avista-gas-daily-*` CSV/ODS pairs
  - `gas_furnace_therms_history.csv`
  - `4BA438-Emporia_Vue-1DAY.ods`, `heat_energy.ods`
  - the 5 `emporia*` folders and zips
  - the 7 `KPUW_*.csv` files
  - `IDGRatehist9126.pdf`, `IDGShortcuts9126.pdf`, and `data/ideratehist9126.pdf`

  `exclude_dates.txt` stays in the root. Nothing is deleted. Anything else unexpected in the project root is listed for the user, not moved.
- [ ] **Step 5: Set `GAS_FILES_DIR = ORIGINAL_FILES_DIR`** in `config.py`.
- [ ] **Step 6: Verify**:
  - `uv run pytest -v`: all pass.
  - `uv run energy sync local`, then the same `sqlite3` count/sum query matches Step 3.
  - `uv run energy day 2022-12-15 --json` matches `data/before_gas.json`.
  - `ls -A` of the project root shows only the files spec §10a says stay.
  - `git status` shows only the `config.py` and test changes.
- [ ] **Step 7: Commit**: `git add src tests && git commit -m "chore: archive original data files; read gas history from archive"`
