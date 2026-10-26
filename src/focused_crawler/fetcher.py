"""Polite HTTP fetcher: robots.txt, per-host delay, retries, HTML-only filter.

Adapted from crawl-ledger's Fetcher (robots cache and per-host politeness)."""

from __future__ import annotations

import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests

from .config import DEFAULT_USER_AGENT

HTML_TYPES = ("text/html", "application/xhtml+xml")
RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    text: str
    last_modified: float | None

    @property
    def ok_html(self) -> bool:
        return self.status == 200 and self.content_type in HTML_TYPES


class Fetcher:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        delay: float = 1.0,
        timeout: float = 15.0,
        max_retries: int = 2,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.user_agent = user_agent
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._sleep = sleep
        self._clock = clock
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.requests_made = 0

    def _polite_wait(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.delay - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_hit[host] = self._clock()

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        if key not in self._robots:
            self._polite_wait(parts.netloc)
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.session.get(f"{key}/robots.txt", timeout=self.timeout)
                self.requests_made += 1
                if r.status_code in (401, 403):
                    rp.disallow_all = True
                else:
                    rp.parse(r.text.splitlines() if r.status_code == 200 else [])
                self._robots[key] = rp
            except requests.RequestException:
                self._robots[key] = None
        rp = self._robots[key]
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    def fetch(self, url: str) -> FetchResult | None:
        """GET a URL politely. Returns None if robots.txt forbids it or the network fails."""
        if not self.allowed(url):
            return None
        host = urlsplit(url).netloc
        for attempt in range(self.max_retries + 1):
            self._polite_wait(host)
            try:
                r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            except requests.RequestException:
                if attempt == self.max_retries:
                    return None
                continue
            self.requests_made += 1
            if r.status_code in RETRY_STATUSES and attempt < self.max_retries:
                continue
            ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            text = r.text if ctype in HTML_TYPES else ""
            return FetchResult(url, r.url, r.status_code, ctype, text, _last_modified(r.headers))
        return None


def _last_modified(headers) -> float | None:
    value = headers.get("Last-Modified")
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None
