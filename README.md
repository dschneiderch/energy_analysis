# energy

Pulls home energy data (ecobee and beestat thermostat runtime, Emporia Vue circuits, Home Assistant grid carbon, Open-Meteo weather, Avista rates and gas history) into one SQLite database. `energy day` then reports a day's kWh, runtime, heating degree days, carbon and cost.

## Setup

```
uv sync
```

Create `.env` in the project root:

```
HA_TOKEN=...
ECOBEE_USERNAME=...
ECOBEE_PASSWORD=...
BEESTAT_API_KEY=...
```

Create `keys.json` (Emporia):

```
{"username": "...", "password": "..."}
```

Run `uv run energy ecobee-login` once before the first sync (needs the authenticator code). Tokens are stored in `.secrets/`.

## Use

```
uv run energy sync [SOURCE ...]     # no source = all sources
uv run energy day YYYY-MM-DD [--json]
```

Sources include `local`, `emporia`, `weather`, `ecobee`, `beestat`, `ha_carbon`; `uv run energy --help` lists them.

## Where data lives

- `data_archive/<source>/`: raw downloads, never overwritten or edited. This is the source of truth and the folder to back up. ecobee, Emporia and Home Assistant data expire upstream, and the beestat API is provisional, so every fetch from them is written here before it is imported (spec section 10a in `docs/superpowers/specs/2026-10-31-energy-data-integration-design.md`). Each beestat sync asks beestat to refresh the two live thermostats (`runtime.sync`), then reads only the last week of daily summaries (beestat can lag up to a week) and archives that raw response. `runtime_thermostat_summary_2026-10-31.json` is the full-history baseline; keep all of them. Weather is fetched again on a rebuild.
- `data/energy.sqlite`: the database, a disposable copy built from the archive, the files below, and the weather API. After a schema change, or if in doubt, delete it and run `uv run energy sync`.
- `data_archive/avista_gas/avista-gas-daily-*.csv`: Avista gas downloads; `gas_daily` is reloaded from them on every `local` sync. Add new downloads as new files. Overlapping files are fine, but two files with different values for the same reading fail the sync.
- `exclude_dates.txt`: hand-maintained not-home date ranges (`YYYY-M-D` or `start -- end`); re-read on every sync. A bad line fails the sync with its line number.
- `rates.csv`: hand-transcribed from Avista's Idaho rate-history PDFs. Update it when rates change; it replaces the `rates` table on every `local` sync, and every effective date needs an `energy` row. download from https://www.myavista.com/about-us/our-rates-and-tariffs/idaho-rates (updated 10-1-2026 https://www.myavista.com/-/media/myavista/content-documents/our-rates-and-tariffs/id/ideratehist10126.pdf)

## Settings (`src/energy/config.py`)

`STRIP_INSTALL_DATE`, `STRIP_KW` (resistance strip install date and power), `LATITUDE`/`LONGITUDE` (weather), `HDD_BASE_F`/`CDD_BASE_F` (degree-day base), `HA_URL` (Home Assistant websocket), plus thermostat/beestat IDs and the Emporia device id.

## Tests

```
uv run pytest                  # offline
uv run pytest -m integration   # hits live APIs, needs credentials
```
