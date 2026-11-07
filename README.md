# CS 6675 Web Crawler

Course project for CS 6675 (Advanced Internet Computing Systems, Fall 2026), made up of three
crawling and serving systems:

| Package | Milestone | What it does | Status |
|---|---|---|---|
| [`focused_crawler`](src/focused_crawler) | M1 | Focused crawler and search engine with profiles. Default: Python documentation (docs.python.org + 19 package docs sites), labeled REFERENCE / TUTORIAL / HOWTO / CHANGELOG, with module, API symbols and version per page. Also: the academic `gatech.edu` profile (COURSE / FACULTY / LAB) | Baseline + Python-docs profile |
| [`crawl_ledger`](src/crawl_ledger) | M2/M3 | Decentralized Python-docs crawler: peers on a Chord ring publish Ed25519-signed, hash-chained records, which are committed in Merkle blocks and audited by re-fetching | Baseline, R1, R2 |
| [`edge_serving`](src/edge_serving) | M4 | Edge caching for verified crawl records: immutable proofs, signed statuses, R1 revocation deltas, R2 Bloom-filter proof sharing | Baseline, R1, R2, HTTP, evaluation (`v0.2.0`) |

## Quick start

```
python -m venv .venv
.venv\Scripts\activate              # Windows; `source .venv/bin/activate` elsewhere
pip install -e ".[dev]"
python -m pytest                    # 172 offline tests
```

```
# M1: crawl Python documentation and search it (data/pydocs.db)
fcrawl crawl --max-pages 500
fcrawl stats
fcrawl search "json.dumps"
# ...or the academic profile (data/crawl.db)
fcrawl --config config/academic.yaml crawl --max-pages 200
fcrawl --config config/academic.yaml search "CS 6675"

# M2/M3: live crawl of PyPI + docs sites, with forging peers and audits
python -m crawl_ledger.sim --peers 20 --dishonest 0.2 --forge-rate 0.2

# M4: compare the serving baseline against R1/R2
python -m edge_serving.sim --compare
python -m edge_serving.http_app demo
```

## Headline results

| System | Result |
|---|---|
| M1 Python-docs crawl (150 pages) | 20 docs sites, 89.2% docs pages (REFERENCE 59, TUTORIAL 29, CHANGELOG 26, HOWTO 18, OTHER 16), 0 errors |
| M1 academic crawl (500 pages) | 98.1% of HTML pages were academic (FACULTY 331, COURSE 87, LAB 41, OTHER 9) |
| M2/M3 live ledger crawl (25 packages) | 44 records, 4/4 forged records caught; 2 honest false FAILs caused by CDN per-response nonces |
| M4 serving simulation | R1 cuts the revoked-record window from up to 1791 s (TTL baseline) to 3 s, with about 75x less gateway load |

Details and caveats: [docs/FINDINGS.md](docs/FINDINGS.md).

## Documentation

- [Architecture](docs/architecture.md): components, data model and protocols for each package
- [Usage](docs/usage.md): every command, option and config key
- [Development](docs/development.md): setup, tests, layout and git workflow
- [Findings](docs/FINDINGS.md) and [M4 results](docs/m4-results.md)
- [Plans](docs/plans/) and the [changelog](CHANGELOG.md)

## Politeness

Both crawlers honor robots.txt, wait at least 1 s between requests to the same host and send an
identifying User-Agent. The M1 crawler stays inside the configured domain and enforces page, depth
and per-host budgets.
