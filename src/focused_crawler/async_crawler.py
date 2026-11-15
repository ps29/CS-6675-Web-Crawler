"""Concurrent crawl loop over per-host queues: many hosts in flight, one request per host.

`Crawler` fetches one page at a time, so a 20-site crawl spends most of its time waiting on one
response while 19 other hosts are idle. `AsyncCrawler` keeps up to `concurrency` requests in
flight, at most one per host, using `HostQueues` for politeness. Everything after the fetch
(classify, extract, index, store, enqueue links) is the same code as the sequential crawler and
runs on the event loop between fetches, so the two crawlers store pages in the same way.

    fcrawl crawl --max-pages 500 --concurrency 20

Politeness is unchanged in kind and slightly stricter in degree: robots.txt is fetched and
honored per host, and a host rests `delay_seconds` after each response (the sequential crawler
counts the delay from the start of the previous request). `AsyncFetcher.request_log` records
the start and end time of every request so that the per-host gaps can be checked.
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.robotparser
from urllib.parse import urlsplit

import aiohttp

from .config import DEFAULT_USER_AGENT, Config
from .crawler import COMMIT_EVERY, SEED_PRIORITY, Crawler, CrawlStats
from .fetcher import HTML_TYPES, RETRY_STATUSES, FetchResult, _last_modified
from .host_queues import HostQueues
from .storage import Storage
from .url_utils import host_of, normalize_url

log = logging.getLogger(__name__)


class AsyncFetcher:
    """aiohttp fetcher. It does not wait between requests: `HostQueues` decides when a host may
    be contacted, and `AsyncCrawler` calls `robots()` and `fetch()` one at a time per host."""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, timeout: float = 15.0,
                 max_retries: int = 2, delay: float = 1.0, limit: int = 100):  # fmt: skip
        self.user_agent = user_agent
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.delay = delay
        self.limit = limit
        self._session: aiohttp.ClientSession | None = None
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.requests_made = 0
        self.request_log: list[tuple[str, float, float]] = []  # (host, start, end), monotonic

    async def __aenter__(self) -> AsyncFetcher:
        # One session for the whole crawl: connections are kept alive and DNS answers cached.
        connector = aiohttp.TCPConnector(limit=self.limit, limit_per_host=1, ttl_dns_cache=300)
        self._session = aiohttp.ClientSession(
            connector=connector, timeout=self.timeout, headers={"User-Agent": self.user_agent}
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session:
            await self._session.close()

    async def _get(self, url: str):
        """One GET, logged. Returns (status, final_url, headers, body bytes)."""
        host = host_of(url)
        start = time.monotonic()
        try:
            async with self._session.get(url, allow_redirects=True) as r:
                body = await r.read()
                self.requests_made += 1
                return r.status, str(r.url), r.headers, body
        finally:
            self.request_log.append((host, start, time.monotonic()))

    def robots_known(self, url: str) -> bool:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}" in self._robots

    async def load_robots(self, url: str) -> None:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        rp = urllib.robotparser.RobotFileParser()
        try:
            status, _, _, body = await self._get(f"{key}/robots.txt")
            if status in (401, 403):
                rp.disallow_all = True
            else:
                rp.parse(body.decode("utf-8", "replace").splitlines() if status == 200 else [])
            self._robots[key] = rp
        except (aiohttp.ClientError, TimeoutError):
            self._robots[key] = None

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        rp = self._robots.get(f"{parts.scheme}://{parts.netloc}")
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    async def fetch(self, url: str) -> FetchResult | None:
        """GET a URL whose host's robots.txt is loaded. None if forbidden or the network fails."""
        if not self.allowed(url):
            return None
        for attempt in range(self.max_retries + 1):
            if attempt:
                await asyncio.sleep(self.delay)  # the host stays busy, so retries are polite too
            try:
                status, final_url, headers, body = await self._get(url)
            except (aiohttp.ClientError, TimeoutError):
                if attempt == self.max_retries:
                    return None
                continue
            if status in RETRY_STATUSES and attempt < self.max_retries:
                continue
            header = headers.get("Content-Type", "")
            ctype = header.split(";")[0].strip().lower()
            text = _decode(body, header) if ctype in HTML_TYPES else ""
            return FetchResult(url, final_url, status, ctype, text, _last_modified(headers))
        return None


def _decode(body: bytes, content_type: str) -> str:
    charset = None
    if "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=")[1].split(";")[0].strip().strip("\"'")
    for encoding in filter(None, (charset, "utf-8")):
        try:
            return body.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", errors="replace")


class AsyncCrawler(Crawler):
    def __init__(self, config: Config, storage: Storage, fetcher=None, classifier=None,
                 concurrency: int | None = None, clock=time.monotonic):  # fmt: skip
        settings = config.crawl
        self.async_fetcher = fetcher or AsyncFetcher(
            settings.user_agent, settings.timeout_seconds, settings.max_retries,
            settings.delay_seconds,
        )  # fmt: skip
        super().__init__(config, storage, fetcher=self.async_fetcher, classifier=classifier)
        self.frontier = HostQueues(settings.max_depth, settings.per_host_max,
                                   settings.host_penalty, settings.delay_seconds)  # fmt: skip
        self.concurrency = concurrency or settings.concurrency
        self._clock = clock
        self.max_in_flight = 0

    def run(self, max_pages: int | None = None) -> CrawlStats:
        return asyncio.run(self.run_async(max_pages))

    async def _visit(self, item):
        """Fetch one URL (loading its host's robots.txt first if needed)."""
        fetcher = self.async_fetcher
        if not fetcher.robots_known(item.url):
            await fetcher.load_robots(item.url)
            await asyncio.sleep(self.frontier.delay)  # rest between robots.txt and the page
        return item, await fetcher.fetch(item.url)

    async def run_async(self, max_pages: int | None = None) -> CrawlStats:
        budget = max_pages or self.config.crawl.max_pages
        for seed in self.config.seeds:
            if norm := normalize_url(seed):
                self._enqueue(norm, SEED_PRIORITY, 0)
        queues: HostQueues = self.frontier
        running: set[asyncio.Task] = set()
        handled = 0
        try:
            async with self.async_fetcher:
                while True:
                    # Start as many requests as the budget, the worker limit and politeness allow.
                    # A request in flight may turn out not to count (robots, network failure), so
                    # the budget is checked against fetched pages plus requests in flight.
                    while (len(running) < self.concurrency
                           and self.stats.fetched + len(running) < budget
                           and (item := queues.pop_ready(self._clock())) is not None):  # fmt: skip
                        running.add(asyncio.create_task(self._visit(item)))
                    self.max_in_flight = max(self.max_in_flight, len(running))
                    if not running:
                        wake = queues.next_ready_time()
                        if self.stats.fetched >= budget or wake is None:
                            break
                        await asyncio.sleep(max(0.0, wake - self._clock()))
                        continue
                    wake = queues.next_ready_time()
                    can_start = (len(running) < self.concurrency
                                 and self.stats.fetched + len(running) < budget)  # fmt: skip
                    timeout = max(0.0, wake - self._clock()) if can_start and wake else None
                    finished, running = await asyncio.wait(
                        running, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in finished:
                        item, result = task.result()
                        queues.done(host_of(item.url), self._clock())
                        try:
                            self._handle(item, result)
                        except Exception:  # one bad page must not stop the crawl
                            self.stats.errors += 1
                            log.exception("failed to process %s", item.url)
                        handled += 1
                        if handled % COMMIT_EVERY == 0:
                            self.storage.commit()
                            log.info("fetched=%d queued=%d in_flight=%d", self.stats.fetched,
                                     len(queues), len(running))  # fmt: skip
        finally:
            for task in running:
                task.cancel()
            self.storage.commit()
        return self.stats
