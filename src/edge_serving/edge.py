"""Edge proxy (tier 2): LRU proof cache, pointer/status caches, EDGE_GET and EDGE_GET_LATEST."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from .objects import NO_STORE, CacheEntry, Response, shared_cache_lifetime

Upstream = Callable[[str], Response]


@dataclass
class EdgeStats:
    hits: int = 0
    misses: int = 0
    upstream_requests: int = 0
    upstream_bytes: int = 0
    served_bytes: int = 0

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class Edge:
    def __init__(
        self,
        name: str,
        upstream: Upstream,
        clock: Callable[[], float],
        proof_capacity: int = 100_000,
    ):
        self.name = name
        self.upstream = upstream
        self.clock = clock
        self.proof_capacity = proof_capacity
        self.proofs: OrderedDict[str, CacheEntry] = OrderedDict()
        self.meta: dict[str, CacheEntry] = {}
        self.stats = EdgeStats()

    def _cache_for(self, path: str):
        return self.proofs if path.startswith("/proof/") else self.meta

    def get(self, path: str) -> Response:
        cache = self._cache_for(path)
        entry = cache.get(path)
        now = self.clock()
        if entry is not None and entry.fresh(now):
            self.stats.hits += 1
            if cache is self.proofs:
                self.proofs.move_to_end(path)
            return self._served(entry.response)
        self.stats.misses += 1
        resp = self.fetch_miss(path)
        self.store(path, resp, now)
        return self._served(resp)

    def fetch_miss(self, path: str) -> Response:
        resp = self.upstream(path)
        self.stats.upstream_requests += 1
        self.stats.upstream_bytes += resp.size_bytes()
        return resp

    def store(self, path: str, resp: Response, now: float) -> None:
        cache = self._cache_for(path)
        lifetime = shared_cache_lifetime(resp.cache_control)
        if resp.status != 200 or lifetime is None:
            cache.pop(path, None)
            return
        cache[path] = CacheEntry(resp, now, lifetime)
        if cache is self.proofs:
            self.proofs.move_to_end(path)
            while len(self.proofs) > self.proof_capacity:
                self.proofs.popitem(last=False)

    def _served(self, resp: Response) -> Response:
        self.stats.served_bytes += resp.size_bytes()
        return resp

    def stamp(self, body: dict) -> dict:
        """Extra fields added to every assembled response (R1 adds synced_epoch)."""
        return body

    def get_latest(self, pkg: str, src: str) -> Response:
        """One round trip: pointer with the proof and status inlined."""
        ptr = self.get(f"/latest/{pkg}/{src}")
        if ptr.status != 200:
            return ptr
        rid = ptr.body["rid"]
        proof = self.get(f"/proof/{rid}")
        if proof.status != 200:
            return Response(proof.status, {"rid": rid, **proof.body}, NO_STORE)
        status = self.get(f"/status/{rid}")
        body = {"rid": rid, "proof": proof.body, "status": status.body}
        return Response(200, self.stamp(body), NO_STORE)
