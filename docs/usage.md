# Usage

All commands assume the virtual environment is active and the package is installed
(`pip install -e ".[dev]"`, see [development.md](development.md)).

## 1. Focused crawler: `fcrawl`

```
fcrawl [--config PATH] [--db PATH] [-v] <command> ...
```

| Command | Options | What it does |
|---|---|---|
| `crawl` | `--max-pages N` | Runs a focused crawl from the configured seeds |
| `search QUERY` | `-k 10`, `--label COURSE\|FACULTY\|LAB\|OTHER`, `--json` | Ranked, type-labeled results |
| `stats` | | JSON summary: pages, indexed, duplicates, hosts, links, distinct terms, label counts |
| `export` | `--format json\|csv`, `--out FILE` | Pages with their extracted fields |

Global options: `--config` (default `config/default.yaml`), `--db` (overrides `storage.db_path`),
`-v` (log every page).

```
fcrawl crawl --max-pages 200
fcrawl stats
fcrawl search "CS 6675"
fcrawl search "machine learning" --label LAB -k 5
fcrawl export --format csv --out data/pages.csv
```

### Config (`config/default.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `domain` | `gatech.edu` | Registered domain; every subdomain is in scope |
| `seeds` | 5 Georgia Tech URLs | Start URLs |
| `crawl.max_pages` | 500 | Page budget |
| `crawl.max_depth` | 6 | Link depth from a seed |
| `crawl.per_host_max` | 200 | Pages per host |
| `crawl.delay_seconds` | 1.0 | Minimum delay between requests to one host |
| `crawl.timeout_seconds`, `crawl.max_retries` | 15.0, 2 | HTTP timeout and retries on 429/5xx |
| `crawl.user_agent` | course-project UA | Identifying User-Agent |
| `storage.db_path` | `data/crawl.db` | SQLite output (gitignored) |
| `selectors` | see file | Extra fields per label: `"css"` for text or `"css@attr"` for an attribute; `"*"` applies to every label |
| `ranking.w_tf`, `w_field`, `w_recency` | 1.0, 3.0, 0.5 | Weights reserved for the metadata-aware ranker (M1 R2, not built yet) |

### Reading the database

`data/crawl.db` is a plain SQLite file. Open it with [DB Browser for SQLite](https://sqlitebrowser.org/)
or the VS Code "SQLite Viewer" extension. The main tables are `pages`, `links`, `courses`,
`faculty`, `labs` and `postings`.

## 2. Crawl ledger (M2/M3)

### Live baseline run: `python -m crawl_ledger.sim`

Crawls PyPI, Read the Docs, self-hosted docs sites and docs.python.org for the packages in
`config/ledger_seeds.txt`, then verifies and audits every record.

| Option | Default | Meaning |
|---|---|---|
| `--peers` | 20 | Peers on the ring |
| `--dishonest` | 0.2 | Share of peers that forge |
| `--forge-rate` | 0.2 | Share of a dishonest peer's records that are forged |
| `--audit-rate` | 1.0 | Share of records audited |
| `--packages`, `--limit` | `config/ledger_seeds.txt`, 25 | Seed file and how many seeds to use |
| `--delay` | 1.0 | Seconds between requests to one host |
| `--wait` | 0 | Seconds between the crawl and the audits (e.g. `86400` to test page drift) |
| `--offline` | off | Use a deterministic fake fetcher (no network) |
| `--seed`, `--out`, `--quiet` | 1, none, off | RNG seed, JSON output path, suppress the per-record log |

```
python -m crawl_ledger.sim --offline
python -m crawl_ledger.sim --peers 20 --dishonest 0.2 --forge-rate 0.2 --out results/m3/run.json
```

The summary JSON reports `records`, `forged_records`, `detection_rate`, `false_rejection_rate`,
`honest_inconclusive_rate`, per-source results, record size, build/verify time and fetch failures.
GitHub READMEs are crawled only when `GITHUB_TOKEN` is set.

### M3 comparison: `python -m crawl_ledger.sim_m3`

Synthetic comparison of the baseline, R1 (Merkle blocks), R2 (deterministic audits + reputation)
and both.

```
python -m crawl_ledger.sim_m3 --compare --peers 100 --dishonest 0.2 --forge-rate 0.05
```

Run `python -m crawl_ledger.sim_m3 --help` for every option, including `--versions`,
`--epoch-size`, `--auditors`, `--change-rate`, `--silent-share`, `--no-collude` and `--live`
(real pages instead of synthetic ones).

## 3. Edge serving (M4)

### Simulation: `python -m edge_serving.sim`

```
python -m edge_serving.sim --compare --out results/m4/compare.json
python -m edge_serving.sim --variant r1 --packages 2000 --proof-capacity 500
```

| Option | Default | Meaning |
|---|---|---|
| `--variant` | `baseline` | `baseline`, `r1`, `r2` or `both` |
| `--compare` | off | Run every variant, and the baseline at each `--t-status` |
| `--packages`, `--peers`, `--edges` | 25, 16, 3 | Scale |
| `--requests`, `--zipf` | 20000, 1.0 | Read trace size and skew |
| `--epochs`, `--epoch-seconds` | 12, 600 | Simulated time |
| `--t-ptr`, `--t-status` | 60, `60 600 3600` | Pointer and status TTLs (seconds) |
| `--release-rate`, `--revoke-rate`, `--pass-rate` | 0.05, 0.02, 0.10 | Per-epoch churn |
| `--gossip-delay` | 5 | Max header gossip delay (s) |
| `--proof-capacity` | 10000 | Edge proof-cache size |
| `--summary-interval`, `--fp-rate` | 300, 0.01 | R2 Bloom summaries |
| `--edge-ms`, `--sibling-ms`, `--gateway-ms` | 5, 40, 150 | Modeled latency |
| `--seed`, `--out` | 0, none | RNG seed, JSON output path |

Metrics: stale accepts, stale window, hit ratio, gateway requests per second, p95 latency, gossip
bytes per edge per epoch, sibling hits.

### HTTP demo: `python -m edge_serving.http_app demo`

Starts a gateway on `:8080` and three edges on `:8081-8083` (localhost), sends 5 concurrent
`/latest/requests/pypi` requests to one edge, verifies the answer and prints the verdict and request
counts. Use `--port` to change the base port.

Endpoints (gateway and edges):

| Path | Returns |
|---|---|
| `GET /latest/{pkg}/{source}` | Pointer, proof and status for the newest record |
| `GET /proof/{record_id}` | Immutable proof |
| `GET /status/{record_id}` | Gateway-signed status |
