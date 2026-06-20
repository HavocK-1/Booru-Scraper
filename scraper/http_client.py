"""
http_client.py — one shared, polite HTTP session.

Why a single session?
  - `requests.Session` reuses the underlying TCP connection (keep-alive), so
    repeated calls to the same host skip the TLS handshake after the first
    request. For a scraper doing thousands of calls to one API, this is a
    big speedup and much gentler on the server.
  - We attach a User-Agent once (some boorus 403 generic python-requests UAs).

Why a global throttle?
  - PLAN.md §6 asks for ~1 req/s per host. We track the timestamp of the last
    request *per host* and sleep the difference before the next call. Doing it
    here (instead of in each scraper) means every source automatically obeys
    the limit and we can't accidentally hammer a site from a bug in one module.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config


class ThrottledSession(requests.Session):
    """A requests.Session that enforces a minimum delay per host.

    The throttle is *per host* so querying gelbooru and danbooru in parallel
    doesn't artificially slow either one down — each host gets its own clock.
    """

    def __init__(self, default_delay: float = 1.0, user_agent: str = config.USER_AGENT):
        super().__init__()
        self.default_delay = default_delay
        # {host: monotonic_timestamp_of_last_request}
        self._last_call: dict[str, float] = {}
        # A lock guards `_last_call` so concurrent download threads can't race
        # and both fire a request before either records its timestamp.
        self._lock = threading.Lock()
        self.headers.update({"User-Agent": user_agent})

        # urllib3 Retry handles transient HTTP errors (429, 5xx) with backoff.
        # total=5, backoff_factor=0.5 -> waits 0.5,1,2,4,8s between retries.
        # We also respect Retry-After headers (status_forcelist).
        retry = Retry(
            total=5,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def _throttle(self, host: str, delay: float) -> None:
        """Sleep just long enough that `delay` seconds passed since last call."""
        with self._lock:
            now = time.monotonic()
            last = self._last_call.get(host, 0.0)
            wait = delay - (now - last)
            if wait > 0:
                time.sleep(wait)
            # Record the time we *actually* fire the request (after sleeping).
            self._last_call[host] = time.monotonic()

    def get(self, url: str, *, delay: float | None = None, **kwargs: Any) -> requests.Response:
        """Throttled GET. `delay` overrides the per-instance default."""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.netloc
        self._throttle(host, self.default_delay if delay is None else delay)
        # timeout=30 protects against hung connections; the server may accept
        # the socket then never respond. Without a timeout the thread blocks
        # forever.
        kwargs.setdefault("timeout", 30)
        # Some image CDNs (e.g. gelbooru's img*.gelbooru.com) employ hotlink
        # protection: a request with no Referer gets an HTML post-view page
        # instead of the image bytes, which then fails md5 verification. Send
        # an origin Referer derived from the host so the CDN treats us as an
        # in-site request. Callers can still override via kwargs["headers"].
        headers = kwargs.setdefault("headers", {})
        headers.setdefault("Referer", f"{parsed.scheme}://{host}/")
        resp = super().get(url, **kwargs)
        return resp


# Module-level singleton. Importing modules do `from .http_client import session`.
# Created lazily-safe at import time — no network happens until first .get().
session = ThrottledSession()
