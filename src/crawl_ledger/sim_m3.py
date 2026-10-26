"""Offline simulation of the M3 design.

Variants: baseline, R1 (block ledger), R2 (deterministic audits), or both.

Sources are synthetic: real package names, fabricated URLs, and an in-process source whose pages can
change between crawl and audit (with or without the source's own change signal). No network is used.

Usage:  python -m crawl_ledger.sim_m3 --compare --peers 100 --dishonest 0.2 --forge-rate 0.05
        python -m crawl_ledger.sim_m3 --variant both --out results/both.json
"""

import argparse
import json
import random
import statistics
import time
from pathlib import Path

from .audit import FAIL, INCONCLUSIVE, PASS, audit
from .blocks import HeaderChain, build_header
from .config import Config
from .fetcher import Fetcher, FetchResult, extract_links, source_urls
from .merkle import merkle_branch, merkle_root
from .peer import Directory, Peer
from .r2 import Reputation, auditors, final_verdict
from .record import sha256_hex
from .ring import Ring
from .verify import verify, verify_record_r1, verify_signature

SOURCES = ("pypi", "rtd", "pydocs", "github")
VARIANTS = ("baseline", "r1", "r2", "both")
T_CRAWL, T_DRIFT = 1000.0, 5000.0  # simulated clock: pages drift after every record was made


class DriftFetcher:
    """Pages have a version; a bumped page returns new content and, unless the change is silent,
    the source's own 'changed at' signal (like a newer PyPI release or Last-Modified)."""

    def __init__(self, page_bytes: int):
        self.page_bytes = page_bytes
        self.version: dict[str, int] = {}
        self.signal: dict[str, float] = {}
        self.requests_made = 0

    def crawl_time(self, i: int, url: str) -> float:
        return T_CRAWL + i * 1e-3

    def mean_page_bytes(self) -> int:
        return self.page_bytes

    def begin_audit_phase(self, urls, rng, a):
        """Pages drift after the crawl."""
        for url in urls:
            if rng.random() < a.change_rate:
                self.version[url] = 1
                if rng.random() >= a.silent_share:
                    self.signal[url] = T_DRIFT

    def fetch(self, url: str) -> FetchResult:
        self.requests_made += 1
        body = f"content of {url} v{self.version.get(url, 0)}".encode()
        body += b"\n" + b"." * max(0, self.page_bytes - len(body))
        return FetchResult(200, body, self.signal.get(url))


class ReplayFetcher:
    """Replays two real snapshots: the crawl, then the audit re-fetch `--wait` seconds later."""

    def __init__(self, crawl_snap, audit_snap):
        self.crawl, self.audit, self.phase = crawl_snap, audit_snap, "crawl"
        self.requests_made = 0

    def crawl_time(self, i: int, url: str) -> float:
        return self.crawl[url][0]

    def mean_page_bytes(self) -> int:
        return round(statistics.mean(len(r.content) for _, r in self.crawl.values()))

    def begin_audit_phase(self, urls, rng, a):
        self.phase = "audit"

    def fetch(self, url: str):
        self.requests_made += 1
        if self.phase == "crawl":
            return self.crawl[url][1]
        return self.audit.get(url)


def live_collect(packages, delay: float, wait: float, log=print):
    """Crawl the real sources politely (robots.txt, per-host delay), wait, then re-fetch once."""
    f = Fetcher(delay=delay)
    crawl_snap, items = {}, []
    for pkg in packages:
        frontier = list(source_urls(pkg).items())
        while frontier:
            src, url = frontier.pop(0)
            if url in crawl_snap:
                continue
            res = f.fetch(url)
            if res is None or res.status != 200:
                log(
                    f"  skip {pkg}/{src}: {'blocked or unreachable' if res is None else res.status}"
                )
                continue
            crawl_snap[url] = (time.time(), res)
            items.append((pkg, src, url))
            log(f"  crawled {pkg}/{src} ({len(res.content)} bytes)")
            frontier += list(extract_links(src, res.content).items())
    if wait:
        log(f"Waiting {wait}s before the audit re-fetch...")
        time.sleep(wait)
    log("Re-fetching for audits...")
    audit_snap = {url: f.fetch(url) for _, _, url in items}
    return items, crawl_snap, audit_snap


def load_packages(path: Path, limit: int) -> list[str]:
    names = [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return names[:limit]


def build_items(packages, versions: int):
    return [
        (pkg, src, f"https://{src}.sim.example/{pkg}/{v}")
        for pkg in packages
        for src in SOURCES
        for v in range(versions)
    ]


def make_peers(n, dishonest, forge_rate, seed):
    n_bad = round(n * dishonest)
    return [
        Peer(
            f"10.0.0.{i}:7000",
            forge_rate=forge_rate if i < n_bad else 0.0,
            rng=random.Random(seed * 1000 + i),
        )
        for i in range(n)
    ]


def pick_proposer(ring, epoch: int, rep: Reputation | None):
    """Owner of the epoch key proposes (option c); with R2, low-reputation peers are skipped."""
    key = sha256_hex(f"epoch:{epoch}".encode())
    for p in ring.successors(key, len(ring.peers)):
        if rep is None or rep.eligible(p.id):
            return p
    return None


def run_variant(variant, peers, ring, directory, items, a, cfg: Config, make_fetcher) -> dict:
    use_r1, use_r2 = variant in ("r1", "both"), variant in ("r2", "both")
    for i, p in enumerate(peers):  # same peers and keys across variants
        p.log, p.rng = [], random.Random(a.seed * 1000 + i)
    drift_rng, sample_rng, pick_rng = (random.Random(a.seed + k) for k in (7, 11, 13))
    fetcher = make_fetcher()
    rep = Reputation([p.id for p in peers], cfg)

    # Crawl: each package is recorded by its ring owner; records are grouped into epochs.
    published = []  # (record, forged, epoch)
    for i, (pkg, src, url) in enumerate(items):
        res = fetcher.fetch(url)
        rec, forged = ring.owner(pkg).make_record(
            pkg, src, url, res.content, now=fetcher.crawl_time(i, url)
        )
        published.append((rec, forged, i // cfg.epoch_size))
    crawl_fetches = fetcher.requests_made

    fetcher.begin_audit_phase(sorted({u for _, _, u in items}), drift_rng, a)

    epochs = sorted({e for _, _, e in published})
    ids_by_epoch = {
        e: sorted(r.id for r, _, ep in published if ep == e and verify_signature(r, directory))
        for e in epochs
    }
    roots = {e: merkle_root(ids_by_epoch[e]) for e in epochs}

    chain, headers, latency, pending = HeaderChain(directory, cfg.final_depth), {}, [], []
    if use_r1:
        for e in epochs:
            proposer = pick_proposer(ring, e, rep if use_r2 else None)
            if proposer is None or not ids_by_epoch[e]:
                continue
            hdr, _ = build_header(e, ids_by_epoch[e], proposer, chain.head)
            assert chain.add(hdr)
            headers[e] = hdr
            pending.append(e)
            for e0 in [x for x in pending if chain.is_final(headers[x])]:
                latency.append(e - e0)
                pending.remove(e0)

    # Audits: the same records are sampled in every variant.
    audit_fetches_before = fetcher.requests_made
    sample = [x for x in published if sample_rng.random() < a.audit_rate]
    rows = []
    for rec, forged, e in sample:
        if use_r2:
            chosen = auditors(rec, roots.get(e + 1, merkle_root([])), ring, cfg.auditors)
        else:
            chosen = [pick_rng.choice([p for p in peers if p.id != rec.peer_id])]
        results = [
            PASS if (forged and a.collude and not p.honest) else audit(rec, directory, fetcher)
            for p in chosen
        ]
        verdict = rep.settle(rec.peer_id, results) if use_r2 else final_verdict(results)
        rows.append({"forged": forged, "verdict": verdict})
    audit_fetches = fetcher.requests_made - audit_fetches_before

    # Consumer verification of every record.
    verify_ms, proof_bytes = [], []
    for rec, _, e in published:
        t = time.perf_counter()
        if use_r1 and e in headers:
            branch = merkle_branch(ids_by_epoch[e], ids_by_epoch[e].index(rec.id))
            ok = verify_record_r1(rec, branch, headers[e], chain, directory)
            proof_bytes.append(rec.size_bytes() + 32 * len(branch) + headers[e].size_bytes())
        else:
            ok = verify(rec, directory)
            proof_bytes.append(rec.size_bytes())
        verify_ms.append(1000 * (time.perf_counter() - t))
        assert ok

    forged_rows = [r for r in rows if r["forged"]]
    honest_rows = [r for r in rows if not r["forged"]]

    def share(rs, v):
        return round(sum(r["verdict"] == v for r in rs) / len(rs), 3) if rs else None

    def mean(xs):
        return round(statistics.mean(xs), 3) if xs else None

    bad = [p.id for p in peers if not p.honest]
    good = [p.id for p in peers if p.honest]
    flagged = rep.flagged()
    return {
        "variant": variant,
        "records": len(published),
        "forged_records": sum(f for _, f, _ in published),
        "audited": len(rows),
        "forged_audited": len(forged_rows),
        "detection_rate": share(forged_rows, FAIL),
        "forged_inconclusive_rate": share(forged_rows, INCONCLUSIVE),
        "false_rejection_rate": share(honest_rows, FAIL),
        "honest_inconclusive_rate": share(honest_rows, INCONCLUSIVE),
        "audit_fetches": audit_fetches,
        "mean_record_bytes": mean([r.size_bytes() for r, _, _ in published]),
        "header_bytes_per_record": round(
            sum(h.size_bytes() for h in headers.values()) / len(published), 2
        ),
        "consumer_bytes_ledger_proof": mean(proof_bytes) if use_r1 else None,
        "consumer_bytes_refetch_page": fetcher.mean_page_bytes(),
        "consumer_verify_ms": round(statistics.mean(verify_ms), 4),
        "latency_epochs_to_final": mean(latency) if use_r1 else None,
        "blocks": len(headers),
        "crawl_fetches": crawl_fetches,
        "reputation": None
        if not use_r2
        else {
            "honest_mean": mean([rep.rep[p] for p in good]),
            "dishonest_mean": mean([rep.rep[p] for p in bad]),
            "dishonest_flagged_inconclusive": len(flagged & set(bad)),
            "honest_flagged_inconclusive": len(flagged & set(good)),
            "dishonest_ineligible": sum(not rep.eligible(p) for p in bad),
        },
    }


def print_table(results):
    cols = [
        ("variant", "variant"),
        ("detection_rate", "detect"),
        ("false_rejection_rate", "false_rej"),
        ("honest_inconclusive_rate", "hon_inc"),
        ("audit_fetches", "aud_fetch"),
        ("consumer_bytes_ledger_proof", "proof_B"),
        ("consumer_bytes_refetch_page", "page_B"),
        ("header_bytes_per_record", "hdr_B/rec"),
        ("latency_epochs_to_final", "latency_ep"),
    ]
    print("  ".join(f"{h:>10}" for _, h in cols))
    for r in results:
        print("  ".join(f"{str(r[k]):>10}" for k, _ in cols))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--variant", choices=VARIANTS, default="both")
    ap.add_argument(
        "--compare", action="store_true", help="run all four variants on the same peers"
    )
    ap.add_argument("--peers", type=int, default=100)
    ap.add_argument("--dishonest", type=float, default=0.2, help="share of peers that forge")
    ap.add_argument(
        "--forge-rate", type=float, default=0.05, help="share of a forger's records that are forged"
    )
    ap.add_argument(
        "--no-collude",
        dest="collude",
        action="store_false",
        help="forgers do not vouch for each other",
    )
    ap.add_argument("--audit-rate", type=float, default=0.2)
    ap.add_argument(
        "--change-rate", type=float, default=0.1, help="share of pages that change after the crawl"
    )
    ap.add_argument(
        "--silent-share", type=float, default=0.0, help="share of changes with no source signal"
    )
    ap.add_argument(
        "--page-bytes",
        type=int,
        default=50_000,
        help="assumed page size (an assumption, not measured)",
    )
    ap.add_argument(
        "--packages",
        default=str(Path(__file__).resolve().parents[2] / "config" / "ledger_seeds.txt"),
    )
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--versions", type=int, default=4, help="URLs per package per source")
    ap.add_argument(
        "--live", action="store_true", help="crawl the real sources instead of simulating pages"
    )
    ap.add_argument(
        "--delay", type=float, default=1.0, help="live: seconds between requests to one host"
    )
    ap.add_argument(
        "--wait",
        type=float,
        default=0.0,
        help="live: seconds between the crawl and the audit re-fetch",
    )
    ap.add_argument("--epoch-size", type=int, default=None, help="override records per block")
    ap.add_argument(
        "--auditors", type=int, default=None, help="override auditors per record under R2"
    )
    ap.add_argument(
        "--config", default="", help="JSON file overriding epoch_size, auditors, reputation weights"
    )
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)

    cfg = Config.from_json(a.config) if a.config else Config()
    peers = make_peers(a.peers, a.dishonest, a.forge_rate, a.seed)
    ring, directory = Ring(peers), Directory(peers)
    if a.epoch_size:
        cfg.epoch_size = a.epoch_size
    if a.auditors:
        cfg.auditors = a.auditors
    packages = load_packages(Path(a.packages), a.limit)
    if a.live:
        items, crawl_snap, audit_snap = live_collect(packages, a.delay, a.wait)

        def make_fetcher():
            return ReplayFetcher(crawl_snap, audit_snap)
    else:
        items = build_items(packages, a.versions)

        def make_fetcher():
            return DriftFetcher(a.page_bytes)

    variants = VARIANTS if a.compare else (a.variant,)
    results = [
        run_variant(v, peers, ring, directory, items, a, cfg, make_fetcher) for v in variants
    ]
    if a.compare:
        print_table(results)
    else:
        print(json.dumps(results[0], indent=2))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(
            json.dumps({"config": vars(a), "m3": vars(cfg), "results": results}, indent=2)
        )
    return results


if __name__ == "__main__":
    main()
