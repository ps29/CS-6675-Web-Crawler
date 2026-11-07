# Ring-wide politeness with host coordinators (results, 2026-11-08)

In M2/M3 each peer waits 1 s between *its own* requests to a host. The prototype runs every peer
in one process with one shared `Fetcher`, so the live runs were polite. In a real deployment each
peer has its own fetcher, though, and N peers can send up to N requests per second to the same
host. pypi.org is the extreme case, since every non-stdlib package's metadata comes from there.

`hostrate.py` gives each host one coordinator, the peer that owns `successor(hash(host))` on the
Chord ring. It holds:

- **A token bucket.** One request per `delay` per host, for crawls and audits alike. A peer asks
  for a lease before every request and sends it at the granted time.
- **Leases at most `horizon` seconds ahead (5 s).** A peer whose slot lies further out is told to
  come back later, and meanwhile it works on another host's task. When a coordinator leaves, its
  successor grants nothing before `horizon` plus one spacing, so old and new leases never come too
  close together.
- **A shared robots.txt cache.** Each host's file is fetched once for the whole ring.
- **A shared validator cache (ETag / Last-Modified).** A re-crawl by any peer can send a
  conditional request and get a 304. Audits fetch unconditionally, because they need the page.

`CoordinatedFetcher` wraps the existing `Fetcher`, which now accepts conditional headers and
returns the validators.

## Method

`python -m crawl_ledger.rate_sim` runs in virtual time against mock hosts, with no network
access.

**Workload.** There are 120 synthetic packages:

- 15% are stdlib modules, with one docs.python.org page.
- The rest have a pypi.org JSON record, and most also have a 4-page docs site. That site is
  either on the package's own Read the Docs subdomain (35%) or on one of 10 shared docs hosts
  (45%).

**Phases.**

- **Phase 1.** Owners crawl their packages, and 20% of records are audited by another peer.
- **Phase 2.** 20% of the peers leave and 10% of the pages change. Each package's current owner
  then re-crawls it conditionally.

**Parameters.** 1 s per host, a 10 ms lease round trip, and 0.2 s per full response (0.05 s for
a 304), each with up to 50% jitter. Means over 3 seeds. Raw outputs are in
`results/m3/host_rate.json` and `results/m3/host_rate_kill.json`.

## Results

| Mode | Peers | Gaps < 1 s | Peak req/s at pypi.org | pypi.org mean req/s | Phase 1 (s) | Phase 1 req/s | Wait p95 (s) | robots.txt fetches | 304 share after churn |
|---|---|---|---|---|---|---|---|---|---|
| independent | 5 | 265 | 4.7 | 0.78 | 174 | 3.9 | 1.0 | 117 | 60% |
| coordinated | 5 | **0** | 1 | 0.55 | 268 | 2.2 | 3.4 | 46 | 90% |
| independent | 20 | 477 | 17.7 | 1.89 | 77 | 9.1 | 1.0 | 165 | 77% |
| coordinated | 20 | **0** | 1 | 0.79 | 176 | 3.3 | 5.0 | 46 | 89% |
| independent | 50 | 637 | 36.3 | 2.46 | 61 | 12.1 | 1.0 | 200 | 75% |
| coordinated | 50 | **0** | 1 | 0.95 | 135 | 4.2 | 5.0 | 46 | 90% |

"Gaps < 1 s" counts consecutive requests to the same host less than 1 s apart, summed over all
hosts. "Peak req/s" is the most requests to pypi.org inside any 1 s window. With 10% of pages
changed, 90% is the highest 304 share possible.

**Coordinator failure** (`--kill-at 30`: pypi.org's coordinator leaves 30 s into phase 1). In
all 9 runs (5/20/50 peers, 3 seeds) there were 0 gaps under 1 s and the peak stayed at 1 request
per second.

## What this shows

- **Independent delays do not bound the aggregate rate.** The peak load on pypi.org grew with
  the ring: 4.7 requests per second with 5 peers, 36 with 50. That breaks the 1 request per second
  per host policy as soon as peers fetch independently.
- **Coordinators keep every host at 1 request per second at every ring size,** including through
  a coordinator failure.
- **Throughput is bounded by the busiest host.** Every package needs pypi.org, so phase 1 cannot
  finish faster than one request per second to it. Adding peers raised coordinated throughput
  from 2.2 to 4.2 requests per second (the other hosts are served in parallel), but it stays below
  the impolite independent rate. The independent baseline is faster only because it overloads
  hosts.
- **Shared caches save requests.** robots.txt was fetched once per host (46) instead of once per
  peer and host (117-200). After 20% churn, re-crawls got 89-90% 304s, against 60-77% when each
  peer keeps its own validators, because new owners inherit the coordinator's cache.
- **The costs.** Lease waits reach the 5 s horizon at the 95th percentile. A coordinator gets
  about 2 messages per request at 20-50 peers, because of refused leases and retries. Load is
  uneven: the pypi.org coordinator handled 250-450 leases of about 1,000 requests.

## Limits

- **Simulated only.** Nothing was measured against real hosts, as the project rules require for
  load experiments. Latencies and the workload mix are assumptions.
- **Graceful leaves only.** A leaving coordinator hands over its caches. A crash would lose the
  robots and validator caches (the next coordinator refetches them), but not the rate guarantee,
  which depends only on the horizon.
- **Honest coordinators are assumed.** A dishonest one could stall a host's crawl by refusing
  leases, or overload it by granting too many. Detecting that would need peers to report lease
  grants, which is not implemented.
- **The live crawler does not use it yet.** `crawl_ledger.sim` still uses one shared `Fetcher`,
  because it runs in one process. `CoordinatedFetcher` is tested only against mock origins.
