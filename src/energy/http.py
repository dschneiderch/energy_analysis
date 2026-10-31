"""HTTP with retry. All network calls in sources/* go through here."""
import time
from urllib.parse import urlsplit

import requests

DEFAULT_TIMEOUT = 120
_BACKOFF = (2, 4)


class HttpError(requests.RequestException):
    """Request failure whose message never contains the query string (it may hold secrets)."""


def _where(method: str, url: str) -> str:
    u = urlsplit(url)
    return f"{method} {u.netloc}{u.path}"


def raise_for_status(resp: requests.Response) -> None:
    """Like Response.raise_for_status but without the URL query string in the message."""
    if resp.status_code >= 400:
        req = getattr(resp, "request", None)
        where = _where(req.method, req.url) if req is not None else "request"
        raise HttpError(f"HTTP {resp.status_code} for {where}") from None


def request(method: str, url: str, *, attempts: int = 3, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    for attempt in range(1, attempts + 1):
        last = attempt == attempts
        failure = None
        try:
            resp = requests.request(method, url, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            failure = f"{type(exc).__name__} for {_where(method, url)}"
        else:
            if resp.status_code < 500:
                return resp
            if last:
                raise HttpError(f"HTTP {resp.status_code} for {_where(method, url)}") from None
        if failure and last:
            # Raised outside the except block so the original exception (whose
            # message may include the query string) is not chained as __context__.
            raise HttpError(failure)
        time.sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])
    raise AssertionError("unreachable")  # pragma: no cover
