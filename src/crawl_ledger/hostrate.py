"""Ring-wide politeness: one rate limit per host, shared by every peer.

Each peer of M2/M3 waits `delay` seconds between its own requests to a host. With N peers
fetching from one host (every package's metadata comes from pypi.org), that is up to N
requests per `delay` in aggregate. Here the peer that owns `successor(hash(host))` on the ring
is that host's *coordinator*. It keeps:

- a token bucket per host. A peer asks for a lease before every request, crawl and audit
  alike, and may send its request at the granted time, never earlier;
- the host's robots.txt, fetched once for the whole ring;
- the latest validators (ETag, Last-Modified) seen per URL, so a re-crawl can send a
  conditional request and get a 304 instead of the page.

Leases are granted at most `horizon` seconds ahead. A peer whose slot lies further out is told
to come back later, so a coordinator never has more than `horizon` seconds of promises
outstanding. When a coordinator leaves the ring, its successor takes over the host and grants
nothing earlier than `horizon` seconds (plus one spacing) after the takeover: every lease the
old coordinator granted lies before that, so the two never book requests too close together.

All times are plain floats, so the same code runs on wall-clock time (`CoordinatedFetcher`) and
on virtual time (`rate_sim.py`)."""

from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .fetcher import USER_AGENT, FetchResult
from .record import sha256_hex


@dataclass
class TokenBucket:
    """Requests to one host spaced at least 1/rate apart (burst 1)."""

    rate: float
    next_free: float = 0.0

    def reserve(self, now: float, horizon: float) -> tuple[bool, float]:
        """(True, slot) and book it, or (False, retry_at) when the slot is beyond `horizon`."""
        slot = max(now, self.next_free)
        if slot - now > horizon:
            return False, slot - horizon
        self.next_free = slot + 1.0 / self.rate
        return True, slot


@dataclass
class Validators:
    etag: str | None = None
    last_modified: str | None = None


@dataclass
class HostCoordinator:
    """The state one peer keeps for the hosts it coordinates."""

    rate: float = 1.0
    horizon: float = 5.0
    buckets: dict[str, TokenBucket] = field(default_factory=dict)
    robots: dict[str, str | None] = field(default_factory=dict)  # host -> robots.txt text
    validators: dict[str, Validators] = field(default_factory=dict)
    leases: int = 0
    refusals: int = 0

    def lease(self, host: str, now: float) -> tuple[bool, float]:
        bucket = self.buckets.setdefault(host, TokenBucket(self.rate))
        ok, t = bucket.reserve(now, self.horizon)
        self.leases += ok
        self.refusals += not ok
        return ok, t

    def take_over(self, host: str, now: float) -> None:
        """Become coordinator of `host`. The old one granted no slot later than now + horizon,
        so the first new slot is one spacing after that."""
        self.buckets[host] = TokenBucket(self.rate, next_free=now + self.horizon + 1.0 / self.rate)


def host_key(host: str) -> str:
    return sha256_hex(f"host:{host}".encode())


class CoordinatorRing:
    """Maps every host to its coordinator peer and moves hosts when a peer leaves."""

    def __init__(self, ring, rate: float = 1.0, horizon: float = 5.0):
        self.ring = ring
        self.rate = rate
        self.horizon = horizon
        self.state: dict[str, HostCoordinator] = {
            p.id: HostCoordinator(rate, horizon) for p in ring.peers
        }
        self.messages = 0  # lease and cache requests sent to coordinators

    def coordinator(self, host: str):
        return self.ring.successor(host_key(host))

    def of(self, host: str) -> HostCoordinator:
        return self.state[self.coordinator(host).id]

    def lease(self, host: str, now: float) -> tuple[bool, float]:
        self.messages += 1
        return self.of(host).lease(host, now)

    def remove_peer(self, peer_id: str, now: float) -> list[str]:
        """The peer left: its hosts move to their new successors. Returns the moved hosts."""
        old = self.state.pop(peer_id)
        self.ring.peers = [p for p in self.ring.peers if p.id != peer_id]
        moved = sorted(set(old.buckets) | set(old.robots) | {urlparse(u).netloc
                                                            for u in old.validators})  # fmt: skip
        for host in moved:
            new = self.of(host)
            new.take_over(host, now)
            # robots.txt and validators are caches: the successor may start empty, but here the
            # leaving peer hands them over (a graceful leave).
            if host in old.robots:
                new.robots[host] = old.robots[host]
        for url, v in old.validators.items():
            self.of(urlparse(url).netloc).validators[url] = v
        return moved


class CoordinatedFetcher:
    """A Fetcher-compatible wrapper for one peer: lease, wait, then fetch (conditionally).

    `fetcher` must accept `fetch(url, headers=...)` and return a FetchResult; `clock` and
    `sleep` default to wall-clock time."""

    def __init__(self, fetcher, coordinators: CoordinatorRing, clock=time.time, sleep=time.sleep,
                 robots_fetch=None):  # fmt: skip
        self.fetcher = fetcher
        self.coordinators = coordinators
        self.clock = clock
        self.sleep = sleep
        self.robots_fetch = robots_fetch  # host -> robots.txt text, or None if unavailable
        self.requests_made = 0
        self.lease_waits: list[float] = []
        self.not_modified = 0

    def _allowed(self, url: str) -> bool:
        if self.robots_fetch is None:
            return True
        host = urlparse(url).netloc
        coord = self.coordinators.of(host)
        if host not in coord.robots:
            self.coordinators.messages += 1
            coord.robots[host] = self.robots_fetch(host)
        text = coord.robots[host]
        if text is None:
            return True
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(text.splitlines())
        return rp.can_fetch(USER_AGENT, url)

    def _wait_for_lease(self, host: str) -> None:
        asked = self.clock()
        while True:
            ok, t = self.coordinators.lease(host, self.clock())
            if t > self.clock():
                self.sleep(t - self.clock())
            if ok:
                break
        self.lease_waits.append(self.clock() - asked)

    def fetch(self, url: str, conditional: bool = False) -> FetchResult | None:
        """conditional=True (re-crawls): send the stored validators and accept a 304. Audits
        use conditional=False, because they need the page itself."""
        if not self._allowed(url):
            return None
        host = urlparse(url).netloc
        self._wait_for_lease(host)
        coord = self.coordinators.of(host)
        headers = {}
        v = coord.validators.get(url)
        if conditional and v is not None:
            if v.etag:
                headers["If-None-Match"] = v.etag
            if v.last_modified:
                headers["If-Modified-Since"] = v.last_modified
        res = self.fetcher.fetch(url, headers=headers)
        self.requests_made += 1
        if res is None:
            return None
        if res.status == 304:
            self.not_modified += 1
        elif res.status == 200 and (res.etag or res.last_modified):
            coord.validators[url] = Validators(res.etag, res.last_modified)
        return res
