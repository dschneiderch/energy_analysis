"""Raw downloads under data_archive/: the source of truth the database is rebuilt from."""
import json
import os
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from energy import store


def write_json(directory: Path, base: str, payload) -> Path:
    """Write a new file <base>_fetched<UTC stamp>.json. Never overwrites: once data ages out
    upstream, the archived file is the only copy."""
    directory.mkdir(parents=True, exist_ok=True)
    base = f"{base}_fetched{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    # A half-written .json would fail every later sync of the source, so write a temp file first.
    tmp = directory / f"{base}.tmp"
    tmp.write_bytes(json.dumps(payload).encode())
    try:
        for i in range(1000):
            path = directory / (f"{base}.json" if i == 0 else f"{base}_{i}.json")
            try:
                os.link(tmp, path)  # appears complete, and fails if the file exists
                return path
            except FileExistsError:
                continue
        raise RuntimeError(f"could not find an unused archive name for {base}")
    finally:
        tmp.unlink()


def _file_key(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def log_imported(conn, source: str, path: Path, rows: int) -> None:
    store.log_sync(conn, source, path.name, _file_key(path), rows, "ok")


def import_new(conn, source: str, paths: Iterable[Path], import_file: Callable[[Path], int]) -> int:
    """Import each file once (tracked in sync_log by name, size and mtime)."""
    total = 0
    for path in paths:
        if conn.execute("SELECT 1 FROM sync_log WHERE source=? AND range_start=? AND range_end=? AND status='ok'",
                        (source, path.name, _file_key(path))).fetchone():
            continue
        n = import_file(path)
        log_imported(conn, source, path, n)
        total += n
    return total
