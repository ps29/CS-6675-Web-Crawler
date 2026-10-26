"""Simulated ring of peers running the baseline: crawl, record, gossip, audit, measure.

Usage:  python -m crawl_ledger.sim --peers 20 --dishonest 0.2 --forge-rate 0.2
        python -m crawl_ledger.sim --offline          (no network; for a quick check)
"""

import argparse
import json
import random
import statistics
import time
from pathlib import Path

from .audit import FAIL, INCONCLUSIVE, PASS, audit
from .fetcher import Fetcher, OfflineFetcher, extract_links, source_urls
from .peer import Directory, Peer
from .ring import Ring
from .verify import verify


def load_seeds(path: Path, limit: int) -> list[str]:
    names = [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return names[:limit]


def crawl(packages, ring, fetcher, log):
    """Each package is crawled by its owner on the ring, as in M2.
    Returns (published records with ground truth, crawl stats)."""
    published, failed, build_times = [], 0, []
    for pkg in packages:
        owner = ring.owner(pkg)
        frontier = list(source_urls(pkg).items())
        while frontier:
            source, url = frontier.pop(0)
            res = fetcher.fetch(url)
            if res is None or res.status != 200:
                failed += 1
                log(f"  skip {pkg}/{source}: {'blocked or error' if res is None else res.status}")
                continue
            t0 = time.perf_counter()
            rec, forged = owner.make_record(pkg, source, url, res.content)
            build_times.append(time.perf_counter() - t0)
            published.append((rec, forged))  # gossip: visible to all peers
            log(f"  {pkg}/{source} by {owner.addr}{'  [forged]' if forged else ''}")
            for src, link in extract_links(source, res.content).items():
                frontier.append((src, link))
    return published, {"fetch_failures": failed, "build_times": build_times}


def run_audits(published, peers, directory, fetcher, audit_rate, rng, log):
    sample = [x for x in published if rng.random() < audit_rate]
    rows = []
    for rec, forged in sample:
        auditor = rng.choice([p for p in peers if p.id != rec.peer_id])
        t0 = time.perf_counter()
        result = audit(rec, directory, fetcher)
        rows.append(
            {
                "pkg": rec.pkg,
                "source": rec.source,
                "forged": forged,
                "owner_honest": directory.by_id[rec.peer_id].honest,
                "auditor": auditor.addr,
                "result": result,
                "age_s": round(time.time() - rec.time, 1),
                "audit_s": round(time.perf_counter() - t0, 3),
            }
        )
        log(f"  audit {rec.pkg}/{rec.source}: {result}{'  (forged)' if forged else ''}")
    return rows


def summarize(published, rows, crawl_stats, verify_times):
    forged_rows = [r for r in rows if r["forged"]]
    honest_rows = [r for r in rows if not r["forged"]]

    def share(rs, res):
        return round(sum(r["result"] == res for r in rs) / len(rs), 3) if rs else None

    by_source = {}
    for r in honest_rows:
        s = by_source.setdefault(r["source"], {"n": 0, PASS: 0, FAIL: 0, INCONCLUSIVE: 0})
        s["n"] += 1
        s[r["result"]] += 1
    return {
        "records": len(published),
        "forged_records": sum(f for _, f in published),
        "audited": len(rows),
        "detection_rate": share(forged_rows, FAIL),  # forged records caught
        "false_rejection_rate": share(honest_rows, FAIL),  # honest records failed
        "honest_inconclusive_rate": share(honest_rows, INCONCLUSIVE),
        "honest_by_source": by_source,
        "mean_record_bytes": round(statistics.mean(r.size_bytes() for r, _ in published), 1)
        if published
        else None,
        "mean_record_build_ms": round(1000 * statistics.mean(crawl_stats["build_times"]), 3)
        if published
        else None,
        "mean_verify_ms": round(1000 * statistics.mean(verify_times), 3) if verify_times else None,
        "fetch_failures": crawl_stats["fetch_failures"],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--peers", type=int, default=20)
    ap.add_argument("--dishonest", type=float, default=0.2, help="share of peers that forge")
    ap.add_argument(
        "--forge-rate", type=float, default=0.2, help="share of a dishonest peer's records forged"
    )
    ap.add_argument("--audit-rate", type=float, default=1.0, help="share of records audited")
    ap.add_argument(
        "--packages",
        default=str(Path(__file__).resolve().parents[2] / "config" / "ledger_seeds.txt"),
    )
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests to one host")
    ap.add_argument("--wait", type=float, default=0.0, help="seconds between crawl and audit")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    log = (lambda *_: None) if a.quiet else print
    rng = random.Random(a.seed)
    n_bad = round(a.peers * a.dishonest)
    peers = [
        Peer(
            f"10.0.0.{i}:7000",
            forge_rate=a.forge_rate if i < n_bad else 0.0,
            rng=random.Random(a.seed * 1000 + i),
        )
        for i in range(a.peers)
    ]
    ring, directory = Ring(peers), Directory(peers)
    fetcher = OfflineFetcher() if a.offline else Fetcher(delay=a.delay)
    packages = load_seeds(Path(a.packages), a.limit)

    log(f"Crawling {len(packages)} packages with {a.peers} peers ({n_bad} dishonest)...")
    t0 = time.time()
    published, crawl_stats = crawl(packages, ring, fetcher, log)
    crawl_s = time.time() - t0

    verify_times = []
    for rec, _ in published:  # consumer-side verification of every record
        t = time.perf_counter()
        assert verify(rec, directory)
        verify_times.append(time.perf_counter() - t)

    if a.wait:
        log(f"Waiting {a.wait}s before audits...")
        time.sleep(a.wait)
    log("Auditing...")
    rows = run_audits(published, peers, directory, fetcher, a.audit_rate, rng, log)

    summary = summarize(published, rows, crawl_stats, verify_times)
    summary.update(
        {"crawl_seconds": round(crawl_s, 1), "requests": fetcher.requests_made, "config": vars(a)}
    )
    print(json.dumps(summary, indent=2))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"summary": summary, "audits": rows}, indent=2))
        log(f"Saved {a.out}")
    return summary


if __name__ == "__main__":
    main()
