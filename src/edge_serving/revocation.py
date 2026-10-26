"""Refinement 1: revocation deltas on the header gossip (M4 Section 3.1).

Each header commits to the epoch's delta with delta_root. Edges follow headers, apply deltas,
and serve statuses and pointers with no TTL; a status is a delta entry plus its Merkle branch,
which the client checks against its own copy of the header chain."""

from __future__ import annotations

from collections.abc import Callable

from crawl_ledger.blocks import Header, HeaderChain
from crawl_ledger.merkle import root_from_branch

from .client import ACCEPT, REFETCH, REJECT, Client
from .delta import Delta, leaf_hash, status_entry
from .edge import Edge
from .objects import INCLUDED, NO_STORE, Proof, Response

DeltaSource = Callable[[int], tuple[Header, Delta] | None]


class R1Edge(Edge):
    """Edge that follows the header gossip; statuses and pointers stay valid until superseded."""

    def __init__(self, name, upstream, clock, chain: HeaderChain, **kwargs):
        super().__init__(name, upstream, clock, **kwargs)
        self.chain = chain
        self.synced_epoch = -1
        self.ttl_fallback = False
        self.statuses: dict[str, dict] = {}
        self.pointers: dict[tuple[str, str], str] = {}
        self.gossip_bytes = 0
        self.rejected_deltas = 0

    def on_header(self, hdr: Header, delta: Delta, fetch_gap: DeltaSource) -> bool:
        """ON_HEADER: validate, fill any gap, check delta_root, apply, advance synced_epoch."""
        if hdr.epoch <= self.synced_epoch:
            return True
        for epoch in range(self.synced_epoch + 1, hdr.epoch):
            missed = fetch_gap(epoch)
            if missed is None or not self._apply(*missed):
                self.ttl_fallback = True
                return False
        return self._apply(hdr, delta)

    def _apply(self, hdr: Header, delta: Delta) -> bool:
        self.gossip_bytes += hdr.size_bytes() + delta.size_bytes()
        if not self.chain.add(hdr) or delta.epoch != hdr.epoch or delta.root() != hdr.delta_root:
            self.rejected_deltas += 1
            return False
        for rid, state in delta.status_changes:
            entry = status_entry(rid, state, delta.epoch)
            self.statuses[rid] = {
                "rid": rid,
                "state": state,
                "epoch": delta.epoch,
                "branch": [[h, left] for h, left in delta.branch(entry)],
            }
        for pkg, src, rid in delta.new_latest:
            self.pointers[(pkg, src)] = rid
        self.synced_epoch = hdr.epoch
        self.ttl_fallback = False
        return True

    def get(self, path: str) -> Response:
        if self.ttl_fallback or not path.startswith(("/status/", "/latest/")):
            return super().get(path)
        self.stats.hits += 1
        parts = path.strip("/").split("/")
        if parts[0] == "latest":
            rid = self.pointers.get((parts[1], parts[2]))
            if rid is None:
                return self._served(Response(404, {"error": "not crawled yet"}))
            return self._served(Response(200, {"rid": rid}, NO_STORE))
        rid = parts[1]
        body = self.statuses.get(rid) or {"rid": rid, "state": INCLUDED, "implied": True}
        return self._served(Response(200, body, NO_STORE))

    def stamp(self, body: dict) -> dict:
        return {**body, "synced_epoch": self.synced_epoch}


class R1Client(Client):
    """CHECK_STATUS_R1: the status branch must hash to delta_root of the client's own header."""

    def header_at(self, epoch: int) -> Header | None:
        cur = self.chain.head
        while cur in self.chain.headers:
            hdr = self.chain.headers[cur]
            if hdr.epoch == epoch:
                return hdr
            cur = hdr.prev
        return None

    def check_r1(self, proof: Proof, status: dict, synced_epoch: int) -> str:
        if not self.verify_proof(proof) or status.get("rid") != proof.record.id:
            return REJECT
        state = status.get("state")
        if status.get("implied"):
            if state != INCLUDED or not self.chain.is_final(proof.header):
                return REJECT
        else:
            hdr = self.header_at(status.get("epoch", -1))
            entry = status_entry(status["rid"], state, status["epoch"])
            branch = [(h, bool(left)) for h, left in status.get("branch", [])]
            if hdr is None or root_from_branch(leaf_hash(entry), branch) != hdr.delta_root:
                return REJECT
        if self.head_epoch() - synced_epoch > self.policy.max_lag:
            return REFETCH
        return ACCEPT if state in self.policy.accept else REJECT

    def check_latest(self, body: dict) -> str:
        try:
            proof = Proof.from_dict(body["proof"])
            return self.check_r1(proof, body["status"], int(body["synced_epoch"]))
        except (KeyError, TypeError, ValueError):
            return REJECT
