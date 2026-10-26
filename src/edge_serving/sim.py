"""Trace-driven evaluation of the M4 serving layer: baseline, +R1, +R2 and both.

A simulated clock drives Zipf-skewed reads through regional edges while the ledger seals an
epoch every --epoch-seconds, crawls new releases and settles audits (some revoking records).
No network is used. Latencies are modeled from per-request hit/sibling/gateway counts.

Usage:  python -m edge_serving.sim --compare --out results/m4/compare.json
"""

from __future__ import annotations

import argparse
import heapq
import json
import random
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from crawl_ledger.blocks import HeaderChain
from crawl_ledger.verify import verify_record_r1

from .client import ACCEPT, IDE_POLICY, Client
from .edge import Edge
from .gateway import Gateway
from .ledger_view import AUDIT_FAIL, AUDIT_PASS, Ledger, build_demo_ledger
from .objects import REVOKED, Proof
from .revocation import R1Client, R1Edge
from .summary import R1R2Edge, R2Edge, SummarySharing

VARIANTS = ("baseline", "r1", "r2", "both")
SEEDS = Path(__file__).resolve().parents[2] / "config" / "ledger_seeds.txt"
SOURCES = ("pypi", "rtd", "pydocs")
REQUEST, EPOCH, GOSSIP, SUMMARY = 0, 1, 2, 3  # event kinds; ties resolve in this order


class SimClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Metrics:
    requests: int = 0
    accepted: int = 0
    stale_accepts: int = 0
    stale_window: float = 0.0
    latencies_ms: list[float] = field(default_factory=list)


def load_packages(n: int) -> list[str]:
    names = [
        line.strip()
        for line in SEEDS.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    names += [f"synthetic-pkg-{i:04d}" for i in range(max(0, n - len(names)))]
    return names[:n]


def zipf_weights(n: int, s: float) -> list[float]:
    return [1.0 / (rank**s) for rank in range(1, n + 1)]


def make_edges(variant: str, a, ledger: Ledger, gateway: Gateway, clock: SimClock) -> list[Edge]:
    def verify(proof: Proof) -> bool:
        return verify_record_r1(
            proof.record, proof.branch, proof.header, ledger.chain, ledger.directory
        )

    def chain() -> HeaderChain:
        return HeaderChain(ledger.directory, ledger.chain.final_depth)

    edges: list[Edge] = []
    for i in range(a.edges):
        name, common = f"edge{i}", {"proof_capacity": a.proof_capacity}
        r2 = {"verify_proof": verify, "summary_capacity": a.proof_capacity, "fp_rate": a.fp_rate}
        if variant == "baseline":
            edges.append(Edge(name, gateway.get, clock, **common))
        elif variant == "r1":
            edges.append(R1Edge(name, gateway.get, clock, chain(), **common))
        elif variant == "r2":
            edges.append(R2Edge(name, gateway.get, clock, **common, **r2))
        else:
            edges.append(R1R2Edge(name, gateway.get, clock, chain(), **common, **r2))
    if variant in ("r2", "both"):
        for e in edges:
            for other in edges:
                if other is not e:
                    e.add_sibling(other, rtt=a.sibling_ms / 1000)
    return edges


def run_variant(variant: str, a, t_status: float) -> dict:
    rng = random.Random(a.seed)
    packages = load_packages(a.packages)
    ledger = build_demo_ledger(packages, SOURCES, n_peers=a.peers, epochs=1, seed=a.seed)
    gateway = Gateway(ledger, t_ptr=a.t_ptr, t_status=t_status)
    clock = SimClock()
    edges = make_edges(variant, a, ledger, gateway, clock)
    r1 = variant in ("r1", "both")
    consumer = (R1Client if r1 else Client)(
        ledger.chain, ledger.directory, gateway.public_key, IDE_POLICY
    )

    def gossip_now(edge) -> None:
        hdr = ledger.headers[-1]
        edge.on_header(hdr, ledger.deltas[hdr.epoch], ledger.header_and_delta)

    if r1:
        ledger.seal_epoch()  # commit pointers that became final while building the ledger
        for e in edges:
            gossip_now(e)

    duration = a.epochs * a.epoch_seconds
    events: list[tuple[float, int, int, object]] = []
    seq = 0

    def push(t: float, kind: int, payload: object = None) -> None:
        nonlocal seq
        heapq.heappush(events, (t, kind, seq, payload))
        seq += 1

    for _ in range(a.requests):
        push(rng.uniform(0, duration), REQUEST)
    for k in range(1, a.epochs + 1):
        push(k * a.epoch_seconds, EPOCH)
    if variant in ("r2", "both"):
        t = a.summary_interval
        while t < duration:
            push(t, SUMMARY)
            t += a.summary_interval

    weights = zipf_weights(len(packages), a.zipf)
    m = Metrics()
    revoked_at: dict[str, float] = {}  # rid -> time the revoking header was sealed
    gateway_requests_before = gateway.requests
    gossip_bytes_before = sum(getattr(e, "gossip_bytes", 0) for e in edges)
    crawl_time = 1e6

    while events:
        t, kind, _, payload = heapq.heappop(events)
        if t > duration:
            break
        clock.now = t
        if kind == EPOCH:
            for pkg in rng.sample(packages, max(1, int(len(packages) * a.release_rate))):
                src = rng.choice(SOURCES)
                crawl_time += 1
                owner = ledger.ring.owner(pkg)
                rec, _ = owner.make_record(
                    pkg, src, f"https://{src}.sim.example/{pkg}/{crawl_time:.0f}", b"v", crawl_time
                )
                ledger.submit(rec)
            final = [rid for rid, s in ledger.state.items() if s == "INCLUDED"]
            n_fail = max(0, round(len(final) * a.revoke_rate))
            failing = set(rng.sample(final, min(n_fail, len(final))))
            for rid in failing:
                ledger.record_audit(rid, AUDIT_FAIL)
            for rid in rng.sample(final, min(len(final), round(len(final) * a.pass_rate))):
                if rid not in failing:
                    ledger.record_audit(rid, AUDIT_PASS)
            ledger.seal_epoch()
            for rid in failing:
                revoked_at.setdefault(rid, t)
            if r1:
                for e in edges:
                    push(t + rng.uniform(0, a.gossip_delay), GOSSIP, e)
        elif kind == GOSSIP:
            gossip_now(payload)
        elif kind == SUMMARY:
            for e in edges:
                if isinstance(e, SummarySharing):
                    e.publish_summary()
        else:
            pkg = rng.choices(packages, weights)[0]
            src = rng.choice(SOURCES)
            edge = rng.choice(edges)
            up0 = edge.stats.upstream_requests
            sib0 = getattr(edge, "sibling_hits", 0)
            resp = edge.get_latest(pkg, src)
            ups = edge.stats.upstream_requests - up0
            sibs = getattr(edge, "sibling_hits", 0) - sib0
            m.latencies_ms.append(a.edge_ms + ups * a.gateway_ms + sibs * a.sibling_ms)
            m.requests += 1
            if resp.status != 200:
                continue
            verdict = consumer.check_latest(resp.body)
            if verdict != ACCEPT:
                continue
            m.accepted += 1
            rid = resp.body["rid"]
            if ledger.state.get(rid) == REVOKED and rid in revoked_at:
                m.stale_accepts += 1
                m.stale_window = max(m.stale_window, t - revoked_at[rid])

    hits = sum(e.stats.hits for e in edges)
    misses = sum(e.stats.misses for e in edges)
    sib_lookups = sum(
        e.sibling_hits + e.sibling_false_positives + e.sibling_bad_copies
        for e in edges
        if isinstance(e, SummarySharing)
    )
    lat = sorted(m.latencies_ms)
    return {
        "variant": variant,
        "t_status": t_status if not r1 else None,
        "requests": m.requests,
        "accepted": m.accepted,
        "stale_accepts": m.stale_accepts,
        "stale_window_s": round(m.stale_window, 1),
        "revoked_records": len(revoked_at),
        "hit_ratio": round(hits / (hits + misses), 4) if hits + misses else 0.0,
        "gateway_requests": gateway.requests - gateway_requests_before,
        "gateway_rps": round((gateway.requests - gateway_requests_before) / duration, 4),
        "latency_p50_ms": lat[len(lat) // 2] if lat else None,
        "latency_p95_ms": lat[int(len(lat) * 0.95)] if lat else None,
        "latency_mean_ms": round(statistics.mean(lat), 2) if lat else None,
        "gossip_bytes_per_edge_epoch": round(
            (sum(getattr(e, "gossip_bytes", 0) for e in edges) - gossip_bytes_before)
            / (len(edges) * a.epochs),
            1,
        )
        if r1
        else 0,
        "sibling_hits": sum(getattr(e, "sibling_hits", 0) for e in edges),
        "sibling_fp_rate": round(
            sum(getattr(e, "sibling_false_positives", 0) for e in edges) / sib_lookups, 4
        )
        if sib_lookups
        else None,
        "inter_edge_bytes": sum(getattr(e, "inter_edge_bytes", 0) for e in edges),
        "summary_bytes": sum(getattr(e, "summary_bytes_sent", 0) for e in edges),
    }


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m edge_serving.sim", description=__doc__)
    ap.add_argument("--variant", choices=VARIANTS, default="baseline")
    ap.add_argument("--compare", action="store_true", help="run every variant and T_status")
    ap.add_argument("--packages", type=int, default=25, help="seeds first, then synthetic names")
    ap.add_argument("--peers", type=int, default=16)
    ap.add_argument("--edges", type=int, default=3)
    ap.add_argument("--requests", type=int, default=20_000)
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--epoch-seconds", type=float, default=600.0)
    ap.add_argument("--zipf", type=float, default=1.0)
    ap.add_argument("--t-ptr", type=float, default=60.0)
    ap.add_argument("--t-status", type=float, nargs="+", default=[60.0, 600.0, 3600.0])
    ap.add_argument("--release-rate", type=float, default=0.05, help="packages re-crawled/epoch")
    ap.add_argument("--revoke-rate", type=float, default=0.02, help="final records revoked/epoch")
    ap.add_argument("--pass-rate", type=float, default=0.10, help="final records passing/epoch")
    ap.add_argument("--gossip-delay", type=float, default=5.0, help="max header gossip delay (s)")
    ap.add_argument("--proof-capacity", type=int, default=10_000)
    ap.add_argument("--summary-interval", type=float, default=300.0)
    ap.add_argument("--fp-rate", type=float, default=0.01)
    ap.add_argument("--edge-ms", type=float, default=5.0)
    ap.add_argument("--sibling-ms", type=float, default=40.0)
    ap.add_argument("--gateway-ms", type=float, default=150.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", help="write results JSON here")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    variants = VARIANTS if a.compare else (a.variant,)
    results = []
    started = time.time()
    for variant in variants:
        ttls = a.t_status if variant in ("baseline", "r2") else [a.t_status[0]]
        for ttl in ttls:
            results.append(run_variant(variant, a, ttl))
    cols = (
        "variant", "t_status", "stale_accepts", "stale_window_s", "hit_ratio", "gateway_rps",
        "latency_p50_ms", "latency_p95_ms", "gossip_bytes_per_edge_epoch", "sibling_hits",
    )  # fmt: skip
    print(" | ".join(cols))
    for r in results:
        print(" | ".join(str(r[c]) for c in cols))
    print(f"({time.time() - started:.1f}s)")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"config": vars(a), "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
