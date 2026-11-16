"""A Mercator-style frontier: priorities in front, one queue per host behind (Heydon & Najork
1999; Manning, Raghavan & Schuetze, Introduction to Information Retrieval, ch. 20).

The sequential `Frontier` hands out the single best URL, and the fetcher then sleeps if that
URL's host was visited less than `delay` seconds ago. Here each host has its own priority queue
and a time before which it must not be contacted again, so a crawler can keep one request in
flight to each of many hosts and never has to sleep while another host is ready.

Rules:
- at most one request to a host is outstanding at a time;
- after a request to a host completes, that host is not contacted again for `delay` seconds
  (counted from completion, which is stricter than the sequential crawler's start-to-start rule);
- among the hosts that are ready, the one whose best URL has the highest effective priority goes
  first, where effective priority = priority - host_penalty * (pages already taken from the host),
  the same rule as `Frontier`;
- the depth limit, the per-host page cap and the URL-seen test are the same as in `Frontier`.

Choosing a host scans the ready hosts, which is fine for the tens of hosts of a focused crawl; a
crawl over many thousands of hosts would keep them in a heap ordered by release time instead.
"""

from __future__ import annotations

import heapq
import itertools
from collections import Counter

from .frontier import FrontierItem
from .url_utils import host_of, url_hash


class HostQueues:
    def __init__(self, max_depth: int, per_host_max: int, host_penalty: float = 0.0,
                 delay: float = 1.0):  # fmt: skip
        self.max_depth = max_depth
        self.per_host_max = per_host_max
        self.host_penalty = host_penalty
        self.delay = delay
        self._queues: dict[str, list[tuple[float, int, FrontierItem]]] = {}
        self._seq = itertools.count()
        self._seen: set[str] = set()
        self._taken: Counter[str] = Counter()
        self._busy: set[str] = set()
        self._release: dict[str, float] = {}  # host -> earliest time of its next request
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def mark_seen(self, url: str) -> bool:
        key = url_hash(url)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def push(self, url: str, priority: float, depth: int, parent: str | None = None) -> bool:
        if depth > self.max_depth or not self.mark_seen(url):
            return False
        item = FrontierItem(url, priority, depth, parent)
        heapq.heappush(
            self._queues.setdefault(host_of(url), []), (-priority, next(self._seq), item)
        )
        self._size += 1
        return True

    def _open(self, host: str) -> bool:
        """A host that still has URLs to give and is under its page cap."""
        return bool(self._queues[host]) and self._taken[host] < self.per_host_max

    def pop_ready(self, now: float) -> FrontierItem | None:
        """The best URL among hosts that may be contacted now; None if no host is ready."""
        best_host, best_key = None, None
        for host, queue in self._queues.items():
            if host in self._busy or not self._open(host) or self._release.get(host, 0.0) > now:
                continue
            neg_priority, seq, _ = queue[0]
            key = (neg_priority + self.host_penalty * self._taken[host], seq)
            if best_key is None or key < best_key:
                best_host, best_key = host, key
        if best_host is None:
            return None
        _, _, item = heapq.heappop(self._queues[best_host])
        self._size -= 1
        self._taken[best_host] += 1
        self._busy.add(best_host)
        return item

    def done(self, host: str, now: float) -> None:
        """A request to `host` completed at `now`; the host rests for `delay` seconds."""
        self._busy.discard(host)
        self._release[host] = now + self.delay

    def hold(self, host: str) -> None:
        """Mark a host busy for a request made outside pop_ready (its robots.txt)."""
        self._busy.add(host)

    def next_ready_time(self) -> float | None:
        """When the next idle host with URLs becomes ready; None if only busy hosts have URLs."""
        times = [self._release.get(h, 0.0) for h in self._queues
                 if h not in self._busy and self._open(h)]  # fmt: skip
        return min(times) if times else None

    def exhausted(self) -> bool:
        """No host has a URL it is still allowed to give."""
        return not any(self._open(h) for h in self._queues)
