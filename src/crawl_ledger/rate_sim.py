"""Politeness at ring scale: independent per-peer delays vs. host coordinators (hostrate.py).

Virtual time and mock hosts only; nothing is fetched from the network. A synthetic workload
looks like the M3 crawl: every non-stdlib package has its metadata on pypi.org, most have a
docs site (their own Read the Docs subdomain, or one of a few shared docs hosts) with 4 pages,
and stdlib modules have one docs.python.org page.

Phase 1: each package's owner crawls it (docs pages after the PyPI record), and a share of
records are audited by another peer; audits are ordinary requests under the same limit.
Phase 2: optionally some peers leave the ring (`--churn`), a share of pages change, and each
package's (possibly new) owner re-crawls it with conditional requests.

Modes:
  independent  each peer waits `--delay` between its own requests to a host (M2/M3)
  coordinated  each request needs a lease from the host's coordinator, successor(hash(host))

    python -m crawl_ledger.rate_sim --peers 5 20 50 --seeds 3 --out results/m3/host_rate.json
"""

from __future__ import annotations

import argparse
import heapq
import json
import random
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from .hostrate import CoordinatorRing
from .peer import Peer
from .ring import Ring

PYPI = "pypi.org"
MODES = ("independent", "coordinated")


@dataclass
class Task:
    kind: str  # crawl | docs | audit | recrawl
    pkg: str
    host: str
    url: str


def workload(n: int, docs_hosts: int, rng: random.Random) -> dict[str, list[tuple[str, str]]]:
    """pkg -> [(host, url), ...]; the first entry is fetched first, the rest after it."""
    out = {}
    for i in range(n):
        name = f"pkg{i:04d}"
        if rng.random() < 0.15:  # a stdlib module
            out[name] = [("docs.python.org", f"https://docs.python.org/3/library/{name}.html")]
            continue
        urls = [(PYPI, f"https://{PYPI}/pypi/{name}/json")]
        r = rng.random()
        host = (f"{name}.readthedocs.io" if r < 0.35
                else f"docs{rng.randrange(docs_hosts)}.example" if r < 0.8 else None)  # fmt: skip
        if host:
            urls += [(host, f"https://{host}/{name}/{page}") for page in ("", "a", "b", "c")]
        out[name] = urls
    return out


class Sim:
    def __init__(self, mode: str, n_peers: int, a, seed: int):
        self.mode, self.a = mode, a
        self.rng = random.Random(seed)
        self.peers = [Peer(f"10.0.{i // 250}.{i % 250}:7000", rng=random.Random(seed * 1000 + i))
                      for i in range(n_peers)]  # fmt: skip
        self.ring = Ring(self.peers)
        self.coords = CoordinatorRing(self.ring, rate=1.0 / a.delay, horizon=a.horizon)
        self.alive = {p.id for p in self.peers}
        self.work = workload(a.packages, a.docs_hosts, self.rng)
        self.queues: dict[str, deque] = {p.id: deque() for p in self.peers}
        self.idle = set(self.alive)
        self.heap: list = []
        self.seq = 0
        self.streak: dict[str, int] = defaultdict(int)
        self.last_hit: dict[str, dict[str, float]] = defaultdict(dict)  # independent mode
        self.peer_validators: dict[str, dict[str, str]] = defaultdict(dict)  # independent mode
        self.robots_seen: set = set()
        self.version: dict[str, int] = defaultdict(int)
        self.host_times: dict[str, list[float]] = defaultdict(list)
        self.waits: list[float] = []
        self.counts = defaultdict(int)
        self.now = 0.0

    # --- scheduling ------------------------------------------------------------------------

    def wake(self, pid: str, t: float) -> None:
        self.seq += 1
        heapq.heappush(self.heap, (t, self.seq, pid))

    def give(self, pid: str, task: Task, t: float) -> None:
        self.queues[pid].append(task)
        if pid in self.idle:
            self.idle.discard(pid)
            self.wake(pid, t)

    def run(self) -> float:
        end = self.now
        while self.heap:
            t, _, pid = heapq.heappop(self.heap)
            self.now = t
            if pid.startswith("kill:"):
                self.kill_coordinator(pid[5:], t)
                continue
            if pid not in self.alive:
                continue
            end = max(end, self.step(pid, t))
        return end

    # --- one peer step ---------------------------------------------------------------------

    def slot(self, pid: str, host: str, t: float) -> tuple[bool, float]:
        """When this peer may send its next request to `host` (independent: its own delay)."""
        if self.mode == "independent":
            start = max(t, self.last_hit[pid].get(host, -1e9) + self.a.delay)
            self.last_hit[pid][host] = start
            return True, start
        t += self.a.rtt  # lease request to the coordinator
        return self.coords.lease(host, t)

    def request(self, host: str, start: float, full: bool) -> float:
        self.host_times[host].append(start)
        lat = self.a.latency if full else self.a.latency_304
        return start + lat * (1 + 0.5 * self.rng.random())

    def step(self, pid: str, t: float) -> float:
        q = self.queues[pid]
        if not q:
            self.idle.add(pid)
            return t
        task = q.popleft()
        ok, start = self.slot(pid, task.host, t)
        if not ok:  # coordinated: slot too far out; try another host's task first
            q.append(task)
            self.streak[pid] += 1
            self.counts["refusals"] += 1
            retry = start if self.streak[pid] > len(q) else t + self.a.rtt
            self.wake(pid, max(retry, t + self.a.rtt))
            return t
        self.streak[pid] = 0
        done = start
        robots_key = task.host if self.mode == "coordinated" else (pid, task.host)
        if robots_key not in self.robots_seen:  # robots.txt first, under the same limit
            self.robots_seen.add(robots_key)
            self.counts["robots_fetches"] += 1
            done = self.request(task.host, start, True)
            if self.mode == "coordinated":
                self.coords.messages += 1  # store robots.txt at the coordinator
            ok, start = self.slot(pid, task.host, done)
            while not ok:
                ok, start = self.slot(pid, task.host, start)
        self.waits.append(start - t)
        etag_now = f"{task.url}#{self.version[task.url]}"
        store = (self.coords.of(task.host).validators if self.mode == "coordinated"
                 else self.peer_validators[pid])  # fmt: skip
        if task.kind == "recrawl" and store.get(task.url) == etag_now:
            self.counts["not_modified"] += 1
            done = self.request(task.host, start, full=False)
        else:
            self.counts["full_" + task.kind] += 1
            done = self.request(task.host, start, full=True)
            store[task.url] = etag_now
        self.on_done(pid, task, done)
        self.wake(pid, done)
        return done

    def on_done(self, pid: str, task: Task, t: float) -> None:
        if task.kind == "crawl":
            for host, url in self.work[task.pkg][1:]:
                self.give(pid, Task("docs", task.pkg, host, url), t)
        if task.kind in ("crawl", "docs") and self.rng.random() < self.a.audit_rate:
            others = sorted(self.alive - {pid})
            if others:
                self.give(self.rng.choice(others), Task("audit", task.pkg, task.host, task.url), t)

    # --- churn -----------------------------------------------------------------------------

    def leave(self, pid: str, t: float) -> None:
        self.alive.discard(pid)
        self.coords.remove_peer(pid, t)  # also removes the peer from the shared ring
        self.peer_validators.pop(pid, None)
        orphans = self.queues.pop(pid)
        for task in orphans:  # queued work moves to the package's new owner
            owner = self.ring.owner(task.pkg).id if task.kind != "audit" else None
            self.give(owner or self.rng.choice(sorted(self.alive)), task, t)

    def kill_coordinator(self, host: str, t: float) -> None:
        pid = self.coords.coordinator(host).id
        self.counts["coordinator_kills"] += 1
        self.leave(pid, t)

    # --- phases ----------------------------------------------------------------------------

    def phase1(self) -> float:
        for pkg, urls in self.work.items():
            host, url = urls[0]
            self.give(self.ring.owner(pkg).id, Task("crawl", pkg, host, url), 0.0)
        if self.a.kill_at:
            self.seq += 1
            heapq.heappush(self.heap, (self.a.kill_at, self.seq, f"kill:{PYPI}"))
        return self.run()

    def phase2(self, t0: float) -> float:
        n_leave = round(self.a.churn * len(self.alive))
        for pid in self.rng.sample(sorted(self.alive), n_leave):
            self.leave(pid, t0)
        for urls in self.work.values():
            for _, url in urls:
                if self.rng.random() < self.a.change_rate:
                    self.version[url] += 1
        self.now = t0
        for pkg, urls in self.work.items():
            owner = self.ring.owner(pkg).id
            for host, url in urls:
                self.give(owner, Task("recrawl", pkg, host, url), t0)
        return self.run()


# --- metrics -------------------------------------------------------------------------------


def peak_window(times: list[float], width: float) -> int:
    times = sorted(times)
    best, j = 0, 0
    for i, t in enumerate(times):
        while times[j] <= t - width + 1e-9:
            j += 1
        best = max(best, i - j + 1)
    return best


def violations(times: list[float], delay: float) -> int:
    times = sorted(times)
    return sum(b - a < delay - 1e-9 for a, b in zip(times, times[1:], strict=False))


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def run_one(mode: str, n_peers: int, a, seed: int) -> dict:
    sim = Sim(mode, n_peers, a, seed)
    end1 = sim.phase1()
    times1 = {h: list(ts) for h, ts in sim.host_times.items()}
    req1 = sum(len(ts) for ts in times1.values())
    end2 = sim.phase2(end1 + 1.0)
    times2 = {h: ts[len(times1.get(h, [])) :] for h, ts in sim.host_times.items()}
    req2 = sum(len(ts) for ts in times2.values())
    all_times = sim.host_times
    pypi = sorted(all_times.get(PYPI, []))
    recrawls = sim.counts["not_modified"] + sim.counts["full_recrawl"]
    leases = [c.leases for c in sim.coords.state.values()]
    return {
        "mode": mode,
        "peers": n_peers,
        "seed": seed,
        "requests": req1 + req2,
        "phase1_s": round(end1, 1),
        "phase1_req_per_s": round(req1 / end1, 2) if end1 else None,
        "phase2_s": round(end2 - end1 - 1.0, 1),
        "phase2_req_per_s": round(req2 / (end2 - end1 - 1.0), 2) if end2 > end1 + 1 else None,
        "violations": sum(violations(ts, a.delay) for ts in all_times.values()),
        "hosts_with_violations": sum(violations(ts, a.delay) > 0 for ts in all_times.values()),
        "peak_per_delay_pypi": peak_window(pypi, a.delay),
        "peak_per_delay_any": max(peak_window(ts, a.delay) for ts in all_times.values()),
        "pypi_req_per_s": round((len(pypi) - 1) / (pypi[-1] - pypi[0]), 3)
        if len(pypi) > 1
        else None,  # fmt: skip
        "wait_p50_s": round(pct(sim.waits, 0.5), 2),
        "wait_p95_s": round(pct(sim.waits, 0.95), 2),
        "robots_fetches": sim.counts["robots_fetches"],
        "audits": sim.counts["full_audit"],
        "not_modified_ratio": round(sim.counts["not_modified"] / recrawls, 3) if recrawls else None,
        "coordinator_messages": sim.coords.messages if mode == "coordinated" else 0,
        "max_leases_one_coordinator": max(leases) if mode == "coordinated" else 0,
        "refusals": sim.counts["refusals"],
        "coordinator_kills": sim.counts["coordinator_kills"],
    }


def aggregate(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        groups[(r["mode"], r["peers"])].append(r)
    out = []
    for (mode, peers), rs in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        agg = {"mode": mode, "peers": peers, "runs": len(rs)}
        for k, v in rs[0].items():
            if k in ("mode", "peers", "seed") or not isinstance(v, int | float) or v is None:
                continue
            vals = [r[k] for r in rs if r[k] is not None]
            agg[k] = round(statistics.mean(vals), 3) if vals else None
        out.append(agg)
    return out


COLUMNS = ("phase1_s", "phase1_req_per_s", "violations", "peak_per_delay_pypi", "pypi_req_per_s",
           "wait_p95_s", "robots_fetches", "not_modified_ratio")  # fmt: skip


def print_table(agg: list[dict]) -> None:
    print("mode".ljust(12) + "peers".rjust(6) + "".join(c[:12].rjust(14) for c in COLUMNS))
    for r in agg:
        cells = "".join(str(r.get(c)).rjust(14) for c in COLUMNS)
        print(r["mode"].ljust(12) + str(r["peers"]).rjust(6) + cells)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m crawl_ledger.rate_sim", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("--peers", type=int, nargs="+", default=[5, 20, 50])
    ap.add_argument("--modes", nargs="+", default=list(MODES), choices=MODES)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--packages", type=int, default=120)
    ap.add_argument("--docs-hosts", type=int, default=10, help="shared self-hosted docs hosts")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests to a host")
    ap.add_argument("--horizon", type=float, default=5.0, help="how far ahead leases are granted")
    ap.add_argument("--rtt", type=float, default=0.01, help="lease round trip, seconds")
    ap.add_argument("--latency", type=float, default=0.2, help="full response time, seconds")
    ap.add_argument("--latency-304", type=float, default=0.05)
    ap.add_argument("--audit-rate", type=float, default=0.2)
    ap.add_argument("--churn", type=float, default=0.2, help="peers leaving before phase 2")
    ap.add_argument("--change-rate", type=float, default=0.1, help="share of pages changed")
    ap.add_argument(
        "--kill-at",
        type=float,
        default=0.0,
        help="remove pypi.org's coordinator at this time in phase 1 (0 = never)",
    )
    ap.add_argument("--out", default="")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    rows = [run_one(m, n, a, s) for n in a.peers for m in a.modes for s in range(1, a.seeds + 1)]
    agg = aggregate(rows)
    print_table(agg)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"config": vars(a), "summary": agg, "runs": rows},
                                          indent=2))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
