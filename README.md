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

- `data/energy.sqlite`: the database (rebuildable from the archive plus the APIs).
- `data_archive/<source>/`: raw downloads, never overwritten (spec section 10a in `docs/superpowers/specs/2026-10-31-energy-data-integration-design.md`).
- `exclude_dates.txt`: hand-maintained not-home date ranges (`YYYY-M-D` or `start -- end`); re-read on every sync. A bad line fails the sync with its line number.
- `rates.csv`: hand-transcribed from Avista's Idaho rate-history PDFs. Update it when rates change; it replaces the `rates` table on every `local` sync, and every effective date needs an `energy` row. download from https://www.myavista.com/about-us/our-rates-and-tariffs/idaho-rates (updated 10-1-2026 https://www.myavista.com/-/media/myavista/content-documents/our-rates-and-tariffs/id/ideratehist10126.pdf)

## Settings (`src/energy/config.py`)

`STRIP_INSTALL_DATE`, `STRIP_KW` (resistance strip install date and power), `LATITUDE`/`LONGITUDE` (weather), `HDD_BASE_F`/`CDD_BASE_F` (degree-day base), `HA_URL` (Home Assistant websocket), plus thermostat/beestat IDs and the Emporia device id.

## Tests

```
uv run pytest                  # offline
uv run pytest -m integration   # hits live APIs, needs credentials
```
