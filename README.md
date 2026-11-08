# CS 6675 Web Crawler

Course project for CS 6675 (Advanced Internet Computing Systems, Fall 2026), made up of three
crawling and serving systems:

| Package | Milestone | What it does | Status |
|---|---|---|---|
| [`focused_crawler`](src/focused_crawler) | M1 | Focused crawler and search engine with profiles. Default: Python documentation (docs.python.org + 19 package docs sites), labeled REFERENCE / TUTORIAL / HOWTO / CHANGELOG, with module, API symbols and version per page. Also: the academic `gatech.edu` profile (COURSE / FACULTY / LAB) | Baseline + Python-docs profile, BM25F ranker (opt-in) |
| [`crawl_ledger`](src/crawl_ledger) | M2/M3 | Decentralized Python-docs crawler: peers on a Chord ring publish Ed25519-signed, hash-chained records, which are committed in Merkle blocks and audited by re-fetching | Baseline, R1, R2, canonical block audits, host coordinators |
| [`edge_serving`](src/edge_serving) | M4 | Edge caching for verified crawl records: immutable proofs, signed statuses, R1 revocation deltas, R2 Bloom-filter proof sharing | Baseline, R1, R2, R1-S state proofs, HTTP, evaluation (`v0.2.0`) |

## Quick start

```
python -m venv .venv
.venv\Scripts\activate              # Windows; `source .venv/bin/activate` elsewhere
pip install -e ".[dev]"
python -m pytest                    # 256 offline tests
```

```
# M1: crawl Python documentation and search it (data/pydocs.db)
fcrawl crawl --max-pages 500
fcrawl stats
fcrawl search "json.dumps"
fcrawl search "how to use fixtures" --ranker bm25f
python -m focused_crawler.evaluate          # P@5 / MRR / nDCG@10 on judged queries
# ...or the academic profile (data/crawl.db)
fcrawl --config config/academic.yaml crawl --max-pages 200
fcrawl --config config/academic.yaml search "CS 6675"

# M2/M3: live crawl of PyPI + docs sites, with forging peers and audits
python -m crawl_ledger.sim --peers 20 --dishonest 0.2 --forge-rate 0.2
python -m crawl_ledger.audit_eval evaluate  # canonical vs. raw audits on re-fetched pages
python -m crawl_ledger.rate_sim             # per-host politeness at ring scale (simulated)

# M4: compare the serving baseline against R1/R2
python -m edge_serving.sim --compare
python -m edge_serving.http_app demo
```

## Headline results

| System | Result |
|---|---|
| M1 Python-docs crawl (150 pages) | 20 docs sites, 89.2% docs pages (REFERENCE 59, TUTORIAL 29, CHANGELOG 26, HOWTO 18, OTHER 16), 0 errors |
| M1 academic crawl (500 pages) | 98.1% of HTML pages were academic (FACULTY 331, COURSE 87, LAB 41, OTHER 9) |
| M2/M3 live ledger crawl (25 packages) | 41 records, 2/2 forged records caught, 0/39 honest false FAILs after canonical hashing (previous run: 4/4 caught, 2/40 false FAILs from CDN per-response nonces) |
| M4 serving simulation | R1 cuts the revoked-record window from up to 1791 s (TTL baseline) to 3 s, with about 75x less gateway load, if edges are honest. A dishonest R1 edge can hide a revocation with no time limit; R1-S state proofs bound it by `max_lag` (about 1800 s), and 5% client spot-checks blocked the lying edge in every run |
| Canonical block audits (104 re-fetched pages) | With synthetic rebuild noise, honest false FAILs 100% (raw hash) → 0%; every sensitive edit inside the main content still caught. Edits to page chrome are not caught; real drift window was minutes |
| Host coordinators (simulated, mock hosts) | Peak load on pypi.org 1 req/s at 5-50 peers, vs. 5-37 req/s with per-peer delays; holds when the coordinator leaves |
| BM25F ranking (48 **draft** judged queries) | nDCG@10 0.557 (tf) → 0.905; the intent and package boosts are not significant vs. plain BM25F. Judgments not yet reviewed |

Details and caveats: [docs/FINDINGS.md](docs/FINDINGS.md).

## Documentation

- [Architecture](docs/architecture.md): components, data model and protocols for each package
- [Usage](docs/usage.md): every command, option and config key
- [Development](docs/development.md): setup, tests, layout and git workflow
- [Findings](docs/FINDINGS.md), [M4 results](docs/m4-results.md), [audit results](docs/audit-results.md),
  [host-rate results](docs/host-rate-results.md) and [ranking results](docs/ranking-results.md)
- [Plans](docs/plans/) and the [changelog](CHANGELOG.md)

## Politeness

Both crawlers honor robots.txt, wait at least 1 s between requests to the same host and send an
identifying User-Agent. The M1 crawler stays inside the configured domain and enforces page, depth
and per-host budgets.
