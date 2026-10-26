"""Priority frontier with a seen-set, depth limit and per-host page cap."""

from __future__ import annotations

import heapq
import itertools
from collections import Counter
from dataclasses import dataclass

from .url_utils import host_of, url_hash


@dataclass(frozen=True)
class FrontierItem:
    url: str
    priority: float
    depth: int
    parent: str | None = None


class Frontier:
    """Highest priority pops first; equal priorities pop in FIFO (breadth-first) order."""

    def __init__(self, max_depth: int, per_host_max: int):
        self.max_depth = max_depth
        self.per_host_max = per_host_max
        self._heap: list[tuple[float, int, FrontierItem]] = []
        self._seq = itertools.count()
        self._seen: set[str] = set()
        self._host_popped: Counter[str] = Counter()

    def __len__(self) -> int:
        return len(self._heap)

    def seen(self, url: str) -> bool:
        return url_hash(url) in self._seen

    def push(self, url: str, priority: float, depth: int, parent: str | None = None) -> bool:
        if depth > self.max_depth:
            return False
        key = url_hash(url)
        if key in self._seen:
            return False
        self._seen.add(key)
        heapq.heappush(
            self._heap, (-priority, next(self._seq), FrontierItem(url, priority, depth, parent))
        )
        return True

    def pop(self) -> FrontierItem | None:
        while self._heap:
            _, _, item = heapq.heappop(self._heap)
            host = host_of(item.url)
            if self._host_popped[host] >= self.per_host_max:
                continue
            self._host_popped[host] += 1
            return item
        return None
