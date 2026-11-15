"""Per-host queues and the async crawler: same pages as the sequential crawler, polite, faster."""

import asyncio
import itertools
import time

from focused_crawler.async_crawler import AsyncCrawler
from focused_crawler.config import Config, CrawlSettings
from focused_crawler.crawler import Crawler
from focused_crawler.fetcher import Fetcher, FetchResult, request_gaps
from focused_crawler.host_queues import HostQueues
from focused_crawler.storage import Storage
from focused_crawler.url_utils import host_of
from tests.fakes import FakeClock, FakeResponse, FakeSession

HOSTS = [f"docs{i}.example.org" for i in range(4)]
DELAY = 0.05


def site() -> dict[str, str]:
    """Four hosts, six pages each; every page links to the next pages of its own host."""
    routes = {}
    for host in HOSTS:
        for n in range(6):
            links = "".join(f'<a href="/reference/p{m}.html">page {m}</a>' for m in range(n + 1, 6))
            routes[f"https://{host}/reference/p{n}.html"] = (
                f"<html><head><title>API reference {n}</title></head><body><h1>Reference {n}</h1>"
                f"<p>function parameter returns module {host} {n}</p>{links}</body></html>"
            )
        routes[f"https://{host}/private/x.html"] = "<html><body>secret</body></html>"
        routes[f"https://{host}/reference/p0.html"] += '<a href="/private/x.html">x</a>'
    return routes


def config(**crawl) -> Config:
    settings = {"max_pages": 100, "delay_seconds": DELAY, "host_penalty": 0.02, **crawl}
    return Config(
        domains=("example.org",),
        seeds=[f"https://{h}/reference/p0.html" for h in HOSTS],
        profile="python_docs",
        crawl=CrawlSettings(**settings),
    )


class FakeAsyncFetcher:
    """Serves the site with a fixed latency and records each request's start and end."""

    def __init__(self, routes, latency=0.02, disallow="/private/"):
        self.routes, self.latency, self.disallow = routes, latency, disallow
        self.request_log: list[tuple[str, float, float]] = []
        self._robots: set[str] = set()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def _wait(self, host):
        start = time.monotonic()
        await asyncio.sleep(self.latency)
        self.request_log.append((host, start, time.monotonic()))

    def robots_known(self, url):
        return host_of(url) in self._robots

    async def load_robots(self, url):
        await self._wait(host_of(url))
        self._robots.add(host_of(url))

    async def fetch(self, url):
        if self.disallow in url:
            return None
        await self._wait(host_of(url))
        if url not in self.routes:
            return FetchResult(url, url, 404, "text/html", "", None)
        return FetchResult(url, url, 200, "text/html", self.routes[url], None)


def crawl_async(concurrency=4, latency=0.02, **crawl):
    fetcher = FakeAsyncFetcher(site(), latency)
    storage = Storage(":memory:")
    crawler = AsyncCrawler(config(**crawl), storage, fetcher=fetcher, concurrency=concurrency)
    start = time.monotonic()
    stats = crawler.run()
    return stats, storage, fetcher, crawler, time.monotonic() - start


def urls(storage):
    return {r["url"] for r in storage.conn.execute("SELECT url FROM pages")}


# --- HostQueues ----------------------------------------------------------------------------


def test_one_request_per_host_and_a_rest_after_each():
    q = HostQueues(max_depth=3, per_host_max=10, delay=1.0)
    q.push("https://a.org/1", 0.9, 0)
    q.push("https://a.org/2", 0.8, 0)
    q.push("https://b.org/1", 0.5, 0)
    assert q.pop_ready(0.0).url == "https://a.org/1"
    assert q.pop_ready(0.0).url == "https://b.org/1"  # a.org is busy, so b.org goes
    assert q.pop_ready(0.0) is None
    q.done("a.org", 2.0)
    assert q.pop_ready(2.5) is None and q.next_ready_time() == 3.0  # resting until 3.0
    assert q.pop_ready(3.0).url == "https://a.org/2"


def test_best_priority_among_ready_hosts_with_host_penalty():
    q = HostQueues(max_depth=3, per_host_max=10, host_penalty=0.3, delay=0.0)
    for url, priority in (("https://a.org/1", 0.9), ("https://a.org/2", 0.9),
                          ("https://b.org/1", 0.7)):  # fmt: skip
        q.push(url, priority, 0)
    assert q.pop_ready(0.0).url == "https://a.org/1"
    q.done("a.org", 0.0)
    assert q.pop_ready(0.0).url == "https://b.org/1"  # a.org's next URL now ranks 0.9 - 0.3


def test_seen_depth_and_per_host_cap():
    q = HostQueues(max_depth=1, per_host_max=1, delay=0.0)
    assert q.push("https://a.org/1", 0.5, 0) and not q.push("https://a.org/1", 0.9, 0)
    assert not q.push("https://a.org/deep", 0.5, 2)
    q.push("https://a.org/2", 0.5, 1)
    assert q.pop_ready(0.0) is not None
    q.done("a.org", 0.0)
    assert q.pop_ready(1.0) is None and q.exhausted()  # the host reached its page cap


# --- AsyncCrawler --------------------------------------------------------------------------


def test_stores_the_same_pages_as_the_sequential_crawler():
    routes = {u: FakeResponse(text=t) for u, t in site().items()}
    for host in HOSTS:
        routes[f"https://{host}/robots.txt"] = FakeResponse(
            text="User-agent: *\nDisallow: /private/\n"
        )
    clock = FakeClock()
    sequential = Storage(":memory:")
    fetcher = Fetcher(session=FakeSession(routes), sleep=clock.sleep, clock=clock, delay=DELAY)
    Crawler(config(), sequential, fetcher=fetcher).run()

    stats, storage, _, _, _ = crawl_async()
    assert stats.errors == 0 and stats.fetched == 24
    assert urls(storage) == urls(sequential)
    assert not any("/private/" in u for u in urls(storage))
    labels = "SELECT url, label, doc_length FROM pages ORDER BY url"
    assert [tuple(r) for r in storage.conn.execute(labels)] == [
        tuple(r) for r in sequential.conn.execute(labels)
    ]


def test_never_two_requests_to_a_host_at_once_and_the_delay_is_kept():
    _, _, fetcher, crawler, _ = crawl_async()
    by_host: dict[str, list[tuple[float, float]]] = {}
    for host, start, end in fetcher.request_log:
        by_host.setdefault(host, []).append((start, end))
    assert len(by_host) == len(HOSTS) and crawler.max_in_flight == len(HOSTS)
    for spans in by_host.values():
        for (_, end), (start, _) in itertools.pairwise(sorted(spans)):
            assert start - end >= DELAY - 0.005  # the host rested after the previous response


def test_hosts_are_fetched_in_parallel():
    # With one worker the 28 requests (24 pages, 4 robots.txt) run one after another.
    _, _, _, _, parallel = crawl_async(concurrency=4, latency=0.08)
    _, _, _, _, one = crawl_async(concurrency=1, latency=0.08)
    assert parallel < one * 0.6


def test_page_budget_is_respected():
    stats, storage, _, _, _ = crawl_async(max_pages=7)
    assert stats.fetched == 7 and len(urls(storage)) == 7


# --- request log ---------------------------------------------------------------------------


def test_request_gaps_summarizes_per_host_gaps_and_overlaps():
    log = [("a.org", 0.0, 0.3), ("b.org", 0.1, 0.2), ("a.org", 1.5, 1.8), ("b.org", 0.15, 0.4)]
    summary = request_gaps(log)
    assert summary["hosts"] == 2 and summary["requests"] == 4
    assert summary["min_start_to_start_s"] == 0.05  # b.org
    assert summary["overlapping_requests_to_one_host"] == 1  # b.org's second began before 0.2
    assert request_gaps([])["min_start_to_start_s"] is None


def test_sequential_fetcher_logs_requests_one_delay_apart():
    routes = {u: FakeResponse(text=t) for u, t in site().items()}
    clock = FakeClock()
    fetcher = Fetcher(session=FakeSession(routes), sleep=clock.sleep, clock=clock, delay=1.0)
    Crawler(config(max_pages=8), Storage(":memory:"), fetcher=fetcher).run()
    summary = request_gaps(fetcher.request_log)
    assert summary["requests"] == fetcher.requests_made == len(fetcher.request_log)
    assert summary["min_start_to_start_s"] >= 1.0
    assert summary["overlapping_requests_to_one_host"] == 0


def test_async_request_log_has_no_overlap_and_keeps_the_delay():
    _, _, fetcher, _, _ = crawl_async()
    summary = request_gaps(fetcher.request_log)
    assert summary["hosts"] == len(HOSTS) and summary["overlapping_requests_to_one_host"] == 0
    assert summary["min_end_to_start_s"] >= DELAY - 0.005
