"""Project constants, paths and secrets loading."""
import json
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "energy.sqlite"
SECRETS_DIR = PROJECT_ROOT / ".secrets"
ARCHIVE_DIR = PROJECT_ROOT / "data_archive"
RATES_CSV = PROJECT_ROOT / "rates.csv"
EXCLUDE_DATES_FILE = PROJECT_ROOT / "exclude_dates.txt"

ECOBEE_API_DIR = ARCHIVE_DIR / "ecobee_api_runtime"
ECOBEE_PORTAL_DIR = ARCHIVE_DIR / "ecobee_portal_csv"
GAS_DIR = ARCHIVE_DIR / "avista_gas"
BEESTAT_DIR = ARCHIVE_DIR / "beestat"
ORIGINAL_FILES_DIR = ARCHIVE_DIR / "original_files"

HA_URL = "ws://192.168.50.206:8123/api/websocket"
LATITUDE = 46.7324
LONGITUDE = -117.0002
LOCAL_TZ = "America/Los_Angeles"

HP_START_DATE = date(2023, 4, 15)
STRIP_INSTALL_DATE = date(2025, 4, 1)
STRIP_KW = 5.0
FURNACE_AFUE = 0.95

HDD_BASE_F = 65
CDD_BASE_F = 65
HEATING_DAY_MIN_HDD = 5

EMPORIA_DEVICE_GID = 219614
HP_CHANNELS = ("7", "8")
MAINS_CHANNEL = "1,2,3"
VIRTUAL_CHANNELS = ("97", "98")

LR_ECOBEE_ID = "415533522899"
UP_ECOBEE_ID = "415522180035"
BEESTAT_LR = 47724
BEESTAT_UP = 47723
BEESTAT_OLD = 47722
LR_BEESTAT_START = date(2023, 10, 30)

KWH_PER_THERM = 29.3071
G_CO2E_PER_THERM = 5306

COMPLETE_FRACTION = 0.95
CALL_MIN_SECONDS = 900
STRIP_RATIO_BAND = (0.5, 1.5)
CARBON_MIN_HOURS = 12


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def load_secrets() -> dict[str, str]:
    """Merge .env key/values with Emporia credentials from keys.json."""
    secrets = _read_env(PROJECT_ROOT / ".env")
    keys = PROJECT_ROOT / "keys.json"
    if keys.exists():
        data = json.loads(keys.read_text())
        secrets["EMPORIA_USERNAME"] = data["username"]
        secrets["EMPORIA_PASSWORD"] = data["password"]
    return secrets
