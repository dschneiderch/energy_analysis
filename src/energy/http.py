"""HTTP with retry. All network calls in sources/* go through here."""
import time

import requests

DEFAULT_TIMEOUT = 120
_BACKOFF = (2, 4)


def request(method: str, url: str, *, attempts: int = 3, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    for attempt in range(1, attempts + 1):
        last = attempt == attempts
        try:
            resp = requests.request(method, url, **kwargs)
        except (requests.ConnectionError, requests.Timeout):
            if last:
                raise
        else:
            if resp.status_code < 500 or last:
                return resp
        time.sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])
    raise AssertionError("unreachable")  # pragma: no cover
