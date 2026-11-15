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
        self.request_log: list[tuple[str, float, float]] = []  # (host, start, end), clock time

    def _get(self, url: str, **kwargs):
        """One GET, logged with its start and end time."""
        start = self._clock()
        try:
            return self.session.get(url, timeout=self.timeout, **kwargs)
        finally:
            self.request_log.append((urlsplit(url).netloc.lower(), start, self._clock()))

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
                r = self._get(f"{key}/robots.txt")
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
                r = self._get(url, allow_redirects=True)
            except requests.RequestException:
                if attempt == self.max_retries:
                    return None
                continue
            self.requests_made += 1
            if r.status_code in RETRY_STATUSES and attempt < self.max_retries:
                continue
            header = r.headers.get("Content-Type", "")
            ctype = header.split(";")[0].strip().lower()
            text = _decode(r, header) if ctype in HTML_TYPES else ""
            return FetchResult(url, r.url, r.status_code, ctype, text, _last_modified(r.headers))
        return None


def request_gaps(log: list[tuple[str, float, float]]) -> dict:
    """Politeness seen from the crawler's side: for each host, the gaps between consecutive
    requests, from start to start and from the end of one to the start of the next. A redirect
    to another host is logged under the host that was asked first."""
    by_host: dict[str, list[tuple[float, float]]] = {}
    for host, start, end in log:
        by_host.setdefault(host, []).append((start, end))
    start_gaps, rest_gaps, overlaps = [], [], 0
    for spans in by_host.values():
        spans.sort()
        for (s0, e0), (s1, _) in zip(spans, spans[1:], strict=False):
            start_gaps.append(s1 - s0)
            rest_gaps.append(s1 - e0)
            overlaps += s1 < e0
    return {
        "hosts": len(by_host),
        "requests": len(log),
        "min_start_to_start_s": round(min(start_gaps), 3) if start_gaps else None,
        "min_end_to_start_s": round(min(rest_gaps), 3) if rest_gaps else None,
        "overlapping_requests_to_one_host": overlaps,
    }


def _decode(r, content_type: str) -> str:
    """Without a charset in Content-Type, requests assumes ISO-8859-1 for text/*; most pages
    are UTF-8, so try that first and fall back to detection."""
    if "charset=" in content_type.lower():
        return r.text
    content = getattr(r, "content", None)
    if content is None:  # test doubles that only carry text
        return r.text
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode(r.apparent_encoding or "utf-8", errors="replace")


def _last_modified(headers) -> float | None:
    value = headers.get("Last-Modified")
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None
