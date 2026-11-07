"""Host coordinators: one rate limit per host for the whole ring, leases, takeover, 304s."""

import random

from crawl_ledger.fetcher import FetchResult
from crawl_ledger.hostrate import CoordinatedFetcher, CoordinatorRing, TokenBucket
from crawl_ledger.peer import Peer
from crawl_ledger.rate_sim import build_parser, peak_window, run_one, violations
from crawl_ledger.ring import Ring


def ring(n=8):
    return Ring([Peer(f"10.0.0.{i}:7000", rng=random.Random(i)) for i in range(n)])


def test_bucket_spaces_requests_and_refuses_beyond_horizon():
    b = TokenBucket(rate=1.0)
    slots = [b.reserve(0.0, horizon=5.0) for _ in range(6)]
    assert [s for ok, s in slots if ok] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    ok, retry = b.reserve(0.0, horizon=5.0)
    assert not ok and retry == 1.0  # slot 6.0 is 5 s out from 1.0


def test_one_coordinator_per_host():
    c = CoordinatorRing(ring())
    assert c.coordinator("pypi.org") is c.coordinator("pypi.org")
    granted = [c.lease("pypi.org", 0.0) for _ in range(3)]
    assert [t for _, t in granted] == [0.0, 1.0, 2.0]


def test_takeover_never_books_too_close_to_old_leases():
    c = CoordinatorRing(ring(), horizon=5.0)
    old = [t for ok, t in (c.lease("pypi.org", 0.0) for _ in range(10)) if ok]
    c.remove_peer(c.coordinator("pypi.org").id, now=0.5)
    ok, first = c.lease("pypi.org", 0.5)
    assert ok is False or first - max(old) >= 1.0
    while not ok:
        ok, first = c.lease("pypi.org", first)
    assert first - max(old) >= 1.0


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Origin:
    """Mock host: ETag "v1"; answers 304 to a matching If-None-Match."""

    def __init__(self, clock):
        self.clock, self.times, self.seen = clock, [], []

    def fetch(self, url, headers=None):
        self.times.append(self.clock())
        self.seen.append(dict(headers or {}))
        if (headers or {}).get("If-None-Match") == '"v1"':
            return FetchResult(304, b"", None, '"v1"')
        return FetchResult(200, b"page", None, '"v1"')


def test_two_peers_share_one_limit_and_one_validator_cache():
    clock, coords = Clock(), CoordinatorRing(ring())
    origin = Origin(clock)
    a = CoordinatedFetcher(origin, coords, clock=clock, sleep=clock.sleep)
    b = CoordinatedFetcher(origin, coords, clock=clock, sleep=clock.sleep)
    for f in (a, b, a, b):
        f.fetch("https://pypi.org/pypi/x/json")
    assert violations(origin.times, 1.0) == 0 and origin.times == [0.0, 1.0, 2.0, 3.0]
    res = b.fetch("https://pypi.org/pypi/x/json", conditional=True)  # a's validators, b's fetch
    assert res.status == 304 and origin.seen[-1]["If-None-Match"] == '"v1"'
    assert origin.seen[0] == {}  # audits and first crawls are unconditional


def test_robots_fetched_once_for_the_ring():
    clock, coords = Clock(), CoordinatorRing(ring())
    calls = []

    def robots(host):
        calls.append(host)
        return "User-agent: *\nDisallow: /private/"

    fs = [CoordinatedFetcher(Origin(clock), coords, clock=clock, sleep=clock.sleep,
                             robots_fetch=robots) for _ in range(3)]  # fmt: skip
    assert all(f.fetch("https://docs.example/a") is not None for f in fs)
    assert fs[0].fetch("https://docs.example/private/x") is None
    assert calls == ["docs.example"]


def test_peak_window_and_violations():
    assert peak_window([0.0, 0.5, 0.9, 2.0], 1.0) == 3
    assert violations([0.0, 1.0, 1.5], 1.0) == 1


def sim(*args):
    return build_parser().parse_args(["--packages", "40", *args])


def test_independent_peers_overload_pypi_and_coordinators_do_not():
    a = sim()
    ind = run_one("independent", 20, a, 1)
    co = run_one("coordinated", 20, a, 1)
    assert ind["violations"] > 0 and ind["peak_per_delay_pypi"] > 1
    assert co["violations"] == 0 and co["peak_per_delay_pypi"] == 1


def test_coordinator_failure_keeps_the_limit():
    r = run_one("coordinated", 20, sim("--kill-at", "10"), 1)
    assert r["coordinator_kills"] == 1 and r["violations"] == 0


def test_shared_validators_survive_churn():
    a = sim("--churn", "0.4", "--change-rate", "0")
    co = run_one("coordinated", 20, a, 1)
    ind = run_one("independent", 20, a, 1)
    assert co["not_modified_ratio"] == 1.0 and ind["not_modified_ratio"] < 1.0
