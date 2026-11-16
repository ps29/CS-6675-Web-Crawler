"""Benchmark the sequential crawler against the async host-queue crawler on a local test web.

    python -m focused_crawler.crawl_bench --hosts 20 --pages 150 --latency 0.3 --repeats 5 \
        --workers 5 10 20 40 --out results/project/crawl_bench.json

The test web is a set of sites served on 127.0.0.1, one port per site, so each site is a separate
host for the crawlers' politeness rules. Every response is delayed by a latency drawn around
`--latency` (0.5x to 1.5x, fixed per URL), and pages are padded to `--page-kb`. The server records
when each request arrived and how many requests each host had open at once, so politeness is
checked from the server's side and not from the crawler's own log.

This measures the crawlers' scheduling under a chosen latency. It does not measure real sites:
the speed-up depends on the latency and the number of hosts given here.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import json
import multiprocessing
import statistics
import tempfile
import time
from pathlib import Path

import requests
from aiohttp import web

from runinfo import provenance

from .async_crawler import AsyncCrawler
from .config import Config, CrawlSettings
from .crawler import Crawler
from .storage import Storage

BASE_PORT = 18700
WORDS = (
    "function module class parameter returns example install tutorial reference guide "
    "release changes deprecated api usage configure request response async index"
).split()
SECTIONS = ("reference", "tutorial", "howto", "changelog")


def _unit(text: str) -> float:
    return int(hashlib.sha1(text.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def page_html(port: int, n: int, hosts: int, pages_per_host: int, page_kb: int) -> str:
    section = SECTIONS[n % len(SECTIONS)]
    targets = {(n * 7 + k) % pages_per_host for k in range(1, 9)} | {(n + 1) % pages_per_host}
    links = [f"/{SECTIONS[m % 4]}/p{m}.html" for m in targets]
    other = BASE_PORT + (port - BASE_PORT + 1 + n) % hosts
    anchors = "".join(f'<li><a href="{u}">{section} page {u}</a></li>' for u in sorted(links))
    # about 8 bytes per word; built in one pass so the server spends its time waiting, not here
    filler = [WORDS[(n * 31 + i * 17 + port) % len(WORDS)] + str((n + i) % 97)
              for i in range(page_kb * 128)]  # fmt: skip
    return (
        f"<html><head><title>{section} page {n} - site {port} documentation</title></head>"
        f"<body><h1>{section} {n}</h1><p>{' '.join(filler)}</p><ul>{anchors}"
        f'<li><a href="http://127.0.0.1:{other}/">other site</a></li></ul></body></html>'
    )


def serve(hosts: int, pages_per_host: int, latency: float, page_kb: int, ready) -> None:
    log: list[tuple[int, float]] = []
    open_now: dict[int, int] = {}
    worst = {"open": 0}

    async def handle(request: web.Request) -> web.Response:
        port = request.url.port
        if request.path == "/__log":
            body = {"requests": log[:], "max_open_per_host": worst["open"]}
            log.clear()
            worst["open"] = 0
            return web.json_response(body)
        log.append((port, time.monotonic()))
        open_now[port] = open_now.get(port, 0) + 1
        worst["open"] = max(worst["open"], open_now[port])
        try:
            await asyncio.sleep(latency * (0.5 + _unit(f"{port}{request.path}")))
            if request.path == "/robots.txt":
                return web.Response(text="User-agent: *\nAllow: /\n")
            name = request.path.rsplit("/", 1)[-1]
            n = int(name[1:-5]) if name.startswith("p") and name.endswith(".html") else 0
            if n >= pages_per_host:
                return web.Response(status=404)
            return web.Response(text=page_html(port, n, hosts, pages_per_host, page_kb),
                                content_type="text/html", charset="utf-8")  # fmt: skip
        finally:
            open_now[port] -= 1

    async def main() -> None:
        app = web.Application()
        app.router.add_get("/{tail:.*}", handle)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        for i in range(hosts):
            await web.TCPSite(runner, "127.0.0.1", BASE_PORT + i).start()
        ready.set()
        await asyncio.Event().wait()

    asyncio.run(main())


def politeness(requests_log: list[list[float]]) -> dict:
    """Smallest gap between the arrival of consecutive requests at the same host."""
    by_host: dict[int, list[float]] = {}
    for port, t in requests_log:
        by_host.setdefault(int(port), []).append(t)
    gaps = [b - a for ts in by_host.values() for a, b in itertools.pairwise(sorted(ts))]
    return {"hosts_contacted": len(by_host), "requests": len(requests_log),
            "min_gap_s": round(min(gaps), 3) if gaps else None}  # fmt: skip


def run_once(workers: int, hosts: int, pages: int, delay: float, per_host_max: int) -> dict:
    """workers = 0 runs the sequential crawler; otherwise the async crawler with that many."""
    config = Config(
        domains=("127.0.0.1",),
        seeds=[f"http://127.0.0.1:{BASE_PORT + i}/" for i in range(hosts)],
        profile="python_docs",
        crawl=CrawlSettings(
            max_pages=pages, per_host_max=per_host_max, host_penalty=0.02, delay_seconds=delay
        ),  # fmt: skip
    )
    control = f"http://127.0.0.1:{BASE_PORT}/__log"
    requests.get(control, timeout=10)  # clear the server log
    with tempfile.TemporaryDirectory() as tmp, Storage(Path(tmp) / "bench.db") as storage:
        crawler = (AsyncCrawler(config, storage, concurrency=workers) if workers
                   else Crawler(config, storage))  # fmt: skip
        start = time.perf_counter()
        stats = crawler.run()
        wall = time.perf_counter() - start
        stored = storage.summary()["pages"]
    seen = requests.get(control, timeout=10).json()
    return {
        "wall_s": round(wall, 2),
        "fetched": stats.fetched,
        "stored": stored,
        "pages_per_s": round(stats.fetched / wall, 2),
        "errors": stats.errors,
        "max_open_per_host": seen["max_open_per_host"],
        **politeness(seen["requests"]),
    }


def rank_sum_p(a: list[float], b: list[float]) -> float:
    """Exact two-sided Mann-Whitney p-value: the share of ways to split the pooled values into
    groups of these sizes whose rank sum is at least as far from its mean as the observed one."""
    pooled = sorted(a + b)
    rank = {v: statistics.mean(i + 1 for i, x in enumerate(pooled) if x == v) for v in set(pooled)}
    ranks = [rank[v] for v in a + b]
    mean = len(a) * (len(ranks) + 1) / 2
    observed = abs(sum(ranks[: len(a)]) - mean)
    splits = list(itertools.combinations(ranks, len(a)))
    return sum(abs(sum(s) - mean) >= observed - 1e-9 for s in splits) / len(splits)


def summarize(runs: list[dict]) -> dict:
    rate = [r["pages_per_s"] for r in runs]
    return {"pages_per_s_median": statistics.median(rate), "pages_per_s_min": min(rate),
            "pages_per_s_max": max(rate),
            "wall_s_median": statistics.median(r["wall_s"] for r in runs),
            "min_gap_s": min(r["min_gap_s"] for r in runs),
            "max_open_per_host": max(r["max_open_per_host"] for r in runs),
            "fetched": sorted({r["fetched"] for r in runs})}  # fmt: skip


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m focused_crawler.crawl_bench", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("--hosts", type=int, default=20)
    ap.add_argument("--pages", type=int, default=150, help="page budget of each crawl")
    ap.add_argument("--pages-per-host", type=int, default=60, help="pages each test site has")
    ap.add_argument("--latency", type=float, default=0.3, help="mean response delay, seconds")
    ap.add_argument("--page-kb", type=int, default=30)
    ap.add_argument("--delay", type=float, default=1.0, help="per-host politeness delay")
    ap.add_argument("--workers", type=int, nargs="+", default=[5, 10, 20, 40])
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--out", default="")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    ready = multiprocessing.Event()
    server = multiprocessing.Process(
        target=serve, args=(a.hosts, a.pages_per_host, a.latency, a.page_kb, ready), daemon=True
    )
    server.start()
    if not ready.wait(30):
        raise SystemExit("the test web server did not start")
    results: dict[str, dict] = {}
    try:
        for workers in [0, *a.workers]:
            name = "sequential" if workers == 0 else f"async-{workers}"
            runs = [run_once(workers, a.hosts, a.pages, a.delay, a.pages_per_host)
                    for _ in range(a.repeats)]  # fmt: skip
            results[name] = {"summary": summarize(runs), "runs": runs}
            s = results[name]["summary"]
            print(f"{name:<12} {s['pages_per_s_median']:6.2f} pages/s "
                  f"[{s['pages_per_s_min']}-{s['pages_per_s_max']}]  min gap {s['min_gap_s']} s  "
                  f"max open per host {s['max_open_per_host']}", flush=True)  # fmt: skip
    finally:
        server.terminate()
    base = [r["pages_per_s"] for r in results["sequential"]["runs"]]
    for name, r in results.items():
        if name != "sequential":
            rates = [x["pages_per_s"] for x in r["runs"]]
            r["summary"]["speedup_median"] = round(
                statistics.median(rates) / statistics.median(base), 2)  # fmt: skip
            r["summary"]["p_vs_sequential"] = round(rank_sum_p(base, rates), 4)
    out = {"provenance": provenance(vars(a)), "config": vars(a), "results": results}
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
