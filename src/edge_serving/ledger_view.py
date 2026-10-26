"""In-process M2/M3 data tier as the gateway sees it: records, blocks, finality, audit verdicts."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from crawl_ledger.blocks import Header, HeaderChain, build_header
from crawl_ledger.merkle import merkle_branch
from crawl_ledger.peer import Directory, Peer
from crawl_ledger.r2 import Reputation
from crawl_ledger.record import Record
from crawl_ledger.ring import Ring

from .delta import Delta
from .objects import INCLUDED, PASS, PENDING, REVOKED, Proof

AUDIT_FAIL, AUDIT_PASS = "FAIL", "PASS"


@dataclass
class EpochChanges:
    """What changed since the previous header: the content of an R1 revocation delta."""

    status_changes: list[tuple[str, str]] = field(default_factory=list)
    new_latest: list[tuple[str, str, str]] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.status_changes or self.new_latest)


class Ledger:
    def __init__(self, peers: list[Peer], final_depth: int = 2):
        self.peers = peers
        self.directory = Directory(peers)
        self.ring = Ring(peers)
        self.chain = HeaderChain(self.directory, final_depth)
        self.reputation = Reputation([p.id for p in peers])
        self.records: dict[str, Record] = {}
        self.state: dict[str, str] = {}
        self.latest: dict[tuple[str, str], str] = {}
        self.header_of: dict[str, Header] = {}
        self.block_ids: dict[str, list[str]] = {}
        self.headers: list[Header] = []
        self._queued: list[str] = []
        self._final: set[str] = set()
        self._changes = EpochChanges()
        self.deltas: dict[int, Delta] = {}
        self.epoch = 0

    # --- writes (crawl peers, block proposers, auditors) ------------------------

    def submit(self, rec: Record) -> None:
        self.records[rec.id] = rec
        self.state[rec.id] = PENDING
        self._queued.append(rec.id)

    def seal_epoch(self, proposer: Peer | None = None) -> Header:
        """Build the next block from queued records; then apply any newly reached finality."""
        proposer = proposer or self.peers[self.epoch % len(self.peers)]
        changes, self._changes = self._changes, EpochChanges()
        delta = Delta(
            self.epoch,
            tuple((rid, s) for rid, s in changes.status_changes if s != INCLUDED),
            tuple(changes.new_latest),
        )
        hdr, ids = build_header(
            self.epoch, self._queued, proposer, self.chain.head, delta_root=delta.root()
        )
        if not self.chain.add(hdr):
            raise RuntimeError("proposed header rejected by the chain")
        self.deltas[hdr.epoch] = delta
        self.headers.append(hdr)
        self.block_ids[hdr.id] = ids
        for rid in ids:
            self.header_of[rid] = hdr
        self._queued = []
        self.epoch += 1
        self._apply_finality()
        return hdr

    def pending_changes(self) -> EpochChanges:
        return self._changes

    def record_audit(self, rid: str, verdict: str) -> str:
        """Settle an audit of a final record. A FAIL majority revokes it."""
        if not self.is_final(rid):
            raise ValueError("only records in final blocks are audited")
        settled = self.reputation.settle(self.records[rid].peer_id, [verdict])
        new = {AUDIT_PASS: PASS, AUDIT_FAIL: REVOKED}.get(settled)
        if new and self.state[rid] != new:
            self.state[rid] = new
            self._changes.status_changes.append((rid, new))
        return self.state[rid]

    def _apply_finality(self) -> None:
        for hdr in self.headers:
            if hdr.id in self._final or not self.chain.is_final(hdr):
                continue
            self._final.add(hdr.id)
            for rid in self.block_ids[hdr.id]:
                if self.state[rid] == PENDING:
                    self.state[rid] = INCLUDED
                    self._changes.status_changes.append((rid, INCLUDED))
                rec = self.records[rid]
                key = (rec.pkg, rec.source)
                current = self.latest.get(key)
                if current is None or self.records[current].time < rec.time:
                    self.latest[key] = rid
                    self._changes.new_latest.append((rec.pkg, rec.source, rid))

    # --- reads (gateway) --------------------------------------------------------

    def is_final(self, rid: str) -> bool:
        hdr = self.header_of.get(rid)
        return hdr is not None and hdr.id in self._final

    def proof(self, rid: str) -> Proof | None:
        hdr = self.header_of.get(rid)
        if hdr is None:
            return None
        ids = self.block_ids[hdr.id]
        return Proof(self.records[rid], merkle_branch(ids, ids.index(rid)), hdr)

    def rep(self, peer_id: str) -> float:
        return round(self.reputation.rep[peer_id], 4)

    def head_epoch(self) -> int:
        return self.headers[-1].epoch if self.headers else -1

    def header_and_delta(self, epoch: int) -> tuple[Header, Delta] | None:
        """Gap fetch for R1 edges that missed an epoch's gossip."""
        if 0 <= epoch < len(self.headers):
            return self.headers[epoch], self.deltas[epoch]
        return None


def build_demo_ledger(
    packages: list[str],
    sources: tuple[str, ...] = ("pypi", "rtd", "pydocs"),
    n_peers: int = 8,
    epochs: int = 3,
    seed: int = 0,
    final_depth: int = 2,
) -> Ledger:
    """A ledger with one record per (package, source) per epoch, then enough empty blocks
    for every record block to become final."""
    rng = random.Random(seed)
    peers = [Peer(f"10.0.0.{i}", rng=rng) for i in range(n_peers)]
    ledger = Ledger(peers, final_depth)
    t = 1000.0
    for epoch in range(epochs):
        for pkg in packages:
            for src in sources:
                owner = ledger.ring.owner(pkg)
                url = f"https://{src}.sim.example/{pkg}/{epoch}"
                rec, _ = owner.make_record(pkg, src, url, f"{pkg} {src} v{epoch}".encode(), now=t)
                ledger.submit(rec)
                t += 1.0
        ledger.seal_epoch()
    for _ in range(final_depth):
        ledger.seal_epoch()
    return ledger
