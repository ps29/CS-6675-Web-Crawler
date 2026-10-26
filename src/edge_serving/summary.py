"""Refinement 2: share proofs between edges through Bloom-filter summaries (M4 Section 3.2).

Only proofs are shared: they are immutable, so a sibling's copy is never stale, and
self-verifying, so a bad copy fails VERIFY_RECORD and the edge falls back to the gateway."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable

from .edge import Edge
from .objects import Proof, Response
from .revocation import R1Edge


class BloomFilter:
    def __init__(self, capacity: int, fp_rate: float):
        capacity = max(capacity, 1)
        self.m = max(8, math.ceil(-capacity * math.log(fp_rate) / math.log(2) ** 2))
        self.k = max(1, round(self.m / capacity * math.log(2)))
        self.bits = bytearray((self.m + 7) // 8)

    def _positions(self, item: str) -> Iterable[int]:
        digest = hashlib.sha256(item.encode()).digest()
        h1 = int.from_bytes(digest[:8], "big")
        h2 = int.from_bytes(digest[8:16], "big") | 1
        return ((h1 + i * h2) % self.m for i in range(self.k))  # Kirsch-Mitzenmacher

    def add(self, item: str) -> None:
        for p in self._positions(item):
            self.bits[p // 8] |= 1 << (p % 8)

    def might_contain(self, item: str) -> bool:
        return all(self.bits[p // 8] & (1 << (p % 8)) for p in self._positions(item))

    def size_bytes(self) -> int:
        return len(self.bits)


class SummarySharing:
    """Mixin for Edge / R1Edge: on a proof miss, ask the nearest sibling whose summary
    may hold the proof, verify the copy, else go to the gateway (PROOF_MISS)."""

    def __init__(
        self,
        *args,
        verify_proof: Callable[[Proof], bool],
        summary_capacity: int = 100_000,
        fp_rate: float = 0.01,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.verify_proof = verify_proof
        self.summary_capacity = summary_capacity
        self.fp_rate = fp_rate
        self.siblings: list[tuple[float, SummarySharing]] = []
        self.sibling_summaries: dict[str, BloomFilter] = {}
        self.sibling_hits = 0
        self.sibling_false_positives = 0
        self.sibling_bad_copies = 0
        self.inter_edge_bytes = 0
        self.summary_bytes_sent = 0

    def add_sibling(self, sibling: SummarySharing, rtt: float) -> None:
        self.siblings.append((rtt, sibling))
        self.siblings.sort(key=lambda pair: pair[0])

    def publish_summary(self) -> None:
        bloom = BloomFilter(self.summary_capacity, self.fp_rate)
        for path in self.proofs:
            bloom.add(path.rsplit("/", 1)[1])
        for _, sib in self.siblings:
            sib.sibling_summaries[self.name] = bloom
            self.summary_bytes_sent += bloom.size_bytes()

    def peek_proof(self, path: str) -> Response | None:
        """Serve a sibling request from cache only; never forwards upstream."""
        entry = self.proofs.get(path)
        return entry.response if entry is not None and entry.fresh(self.clock()) else None

    def fetch_miss(self, path: str) -> Response:
        if path.startswith("/proof/"):
            rid = path.rsplit("/", 1)[1]
            for _, sib in self.siblings:
                summary = self.sibling_summaries.get(sib.name)
                if summary is None or not summary.might_contain(rid):
                    continue
                resp = sib.peek_proof(path)
                if resp is None:
                    self.sibling_false_positives += 1
                    break
                self.inter_edge_bytes += resp.size_bytes()
                if self._valid_copy(rid, resp):
                    self.sibling_hits += 1
                    return resp
                self.sibling_bad_copies += 1
                break
        return super().fetch_miss(path)

    def _valid_copy(self, rid: str, resp: Response) -> bool:
        try:
            proof = Proof.from_dict(resp.body)
        except (KeyError, TypeError):
            return False
        return proof.record.id == rid and self.verify_proof(proof)


class R2Edge(SummarySharing, Edge):
    pass


class R1R2Edge(SummarySharing, R1Edge):
    pass
