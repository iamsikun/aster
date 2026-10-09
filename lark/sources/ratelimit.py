"""Per-host rate limits shared by every collector in the process."""

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

# Minimum seconds between requests to a host. arXiv: 1 request / 3 s, one connection.
DEFAULT_INTERVALS: dict[str, float] = {
    "export.arxiv.org": 3.0,
    "arxiv.org": 3.0,
    "rss.arxiv.org": 3.0,
    "oaipmh.arxiv.org": 3.0,
    "api.crossref.org": 0.2,
    "api.openalex.org": 0.15,
    "api2.openreview.net": 1.0,
    "api.github.com": 0.5,
    "huggingface.co": 0.5,
}


class RateLimiter:
    def __init__(self, intervals: dict[str, float] | None = None, default: float = 0.5):
        self.intervals = {**DEFAULT_INTERVALS, **(intervals or {})}
        self.default = default
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def wait(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        interval = self.intervals.get(host, self.default)
        with self._lock_for(host):
            last = self._last.get(host, 0.0)
            delay = last + interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()


LIMITER = RateLimiter()
