# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- `corpus_jobs.hadoop.parse_counters` added every report of a counter in the log; the local job
  runner prints each counter twice, so every local-mode Hadoop counter (including those reported
  for M5) was exactly twice its real value. It now takes the job total. `docs/m5-results.md` has
  a correction note; times and ratios were not affected.

### Added
- Term project (branch `feat/project-pipeline`): the index job feeds the search engine
  (`corpus_jobs.load`), an async crawler with per-host queues (`fcrawl crawl --concurrency`,
  `--request-log`, `focused_crawler.crawl_bench`), an index built from verified ledger records
  (`crawl_ledger.snapshot`, `corpus ledger`, `load --from-corpus`), a compact LSH shuffle
  (`hadoop-lsh`: band shuffle 1.36 GB → 29 MB, 210.6 s → 199.1 s on 81,842 pages), a
  duplicate-collapsing ranking experiment (`corpus_jobs.dup_eval`), and a two-worker HDFS/YARN
  cluster on one machine (`corpus_jobs.cluster`, `--hdfs`). Results in
  `docs/project-results.md`, `docs/project-ledger-index.md` and `docs/project-refinements.md`.
- `docs/m5-reproduce.md`: what the M5 work did, the machine it ran on, and step-by-step
  instructions to repeat it (Java, Hadoop, Common Crawl files and corpus checksums).
- `docs/crawler-optimization.md`: a live 500-page run of the M1 crawler (356 s, 1.4 pages/s, no
  errors) and a prioritized optimization plan; the crawl is limited by sequential fetching.

### Changed
- M5 benchmark finished: all five engines measured on the 81,842-page corpus (3 runs at 2 cores)
  and on 1 core for the easy and average corpora, replacing the hard-corpus estimate
  (`docs/m5-results.md`). Single node 250 s, Spark 258 s, refined Hadoop 334 s, naive Hadoop
  609 s; near-duplicates at 94.6% precision and 95.0% recall.

### Fixed
- `corpus_jobs.hadoop`: naive Hadoop ran out of Java heap on the hard corpus because the local
  runner runs both reducers in one JVM. The reducer shuffle buffer is now 0.35 of the heap
  (default 0.70); the average-corpus Hadoop runs were repeated with it (naive 189 s → 174 s,
  refined 110 s → 109 s). A failed job now writes its full stderr next to the output.

### Added
- `corpus_jobs` (M5): inverted index, top-1000 terms and MinHash/LSH near-duplicate pairs from a
  crawl snapshot on a single node, Hadoop 3.x Streaming (naive baseline and a refined version with
  in-mapper combining, a combiner and local top-k) and Spark (cached and uncached). A corpus
  builder for Common Crawl WET files and the M3 drift pages, with injected near-copies as ground
  truth; a benchmark with output checks and peak memory; an estimator for unrun scenarios; GUI
  entries. Preliminary results from a few runs (`docs/m5-results.md`): on 20,205 pages the refined
  Hadoop job is 42% faster than the naive one, Spark 35% faster than refined Hadoop, and all
  outputs are identical; near-duplicates at 93% precision and 94% recall. The 81,842-page corpus is
  estimated, not run. Optional dependencies: `pip install -e ".[m5]"`.
- `lab_gui`: a Windows GUI (Tkinter, `lab-gui` or `python -m lab_gui`) that runs every
  experiment, test suite and lint check. Option forms are generated from each program's
  argparse parser, output streams live with Stop, the exact command can be copied, and a
  Results tab browses `results/`. `crawl_ledger.sim`, `sim_m3`, `audit_eval`,
  `focused_crawler.evaluate` and `edge_serving.http_app` now expose `build_parser()`.
- `focused_crawler` profiles. The new default `python_docs` profile crawls docs.python.org and
  19 package docs sites, labels pages REFERENCE / TUTORIAL / HOWTO / CHANGELOG, and stores package,
  version, module and API symbols in a `doc_pages` table. The M1 academic crawl moved to
  `config/academic.yaml` (`profile: academic`).
- Config: `domains` (several scopes, each optionally with a path prefix), `exclude` regexes,
  `crawl.host_penalty` (spreads a multi-site budget over hosts).
- Indexer: dotted names (`json.dumps`) are also indexed as one joined token.
- `crawl_ledger` link extractor follows self-hosted documentation sites (source `docs`) and
  `readthedocs.org` links. The live crawl went from 2 to 17 docs records (`results/m3/live_run4.json`).
- Project documentation: `docs/architecture.md`, `docs/usage.md`, `docs/development.md`, a docs
  index, and a rewritten top-level README.
- `edge_serving` dishonest edges (`--dishonest-edges`, `--attack`): baseline status replay and R1
  `freeze` / `withhold`. Under attack R1's stale window has no bound, while the baseline's stops
  near 1800 s (`docs/m4-results.md`, `results/m4/dishonest_edges.json`).
- `edge_serving` R1-S (`--variant r1s`, `state_proofs.py`): proofs of non-revocation. Headers can
  carry an optional `state_root` over every final record's state, which enters the header ID only
  when set. A lying edge's stale window is now bounded by `max_lag` (about 1800 s) instead of
  unbounded, and withholding fails safe (`results/m4/state_proofs.json`).
- `edge_serving` client spot-checks (`--spot-check`, `--spot-strikes`) and a `replay` attack for
  R1-family edges. At 5% the lying edge was blocked in every run.
- `crawl_ledger` canonical, block-level audits (`canonical.py`, `canonical_audit.py`): records can
  commit to a versioned Merkle root of content blocks (optional, signed `canon` field). An audit
  FAILs only when a sensitive block (code, link, install command, version, metadata field)
  differs, and needs two agreeing auditor fetches. With synthetic rebuild noise, honest false
  FAILs went from 100% (raw hash) to 0%, and every sensitive content edit was still detected
  (`docs/audit-results.md`, `results/m3/audit_eval.json`).
- `python -m crawl_ledger.audit_eval` (collect / evaluate), a resumable, polite drift collector
  and a forgery suite (`forgery.py`) that compares raw, canonical, block and SimHash audits.
- `crawl_ledger` host coordinators (`hostrate.py`): `successor(hash(host))` keeps one token
  bucket per host for the whole ring (crawls and audits), short leases that survive a
  coordinator leaving, and shared robots.txt and ETag/Last-Modified caches. `Fetcher.fetch`
  accepts conditional headers and returns validators. In a virtual-time simulation against mock
  hosts (`python -m crawl_ledger.rate_sim`), the peak load on pypi.org stayed at 1 req/s with
  5-50 peers, vs. 4.7-36 req/s with per-peer delays (5-37 req/s after seeding keys) (`docs/host-rate-results.md`).
- `focused_crawler` BM25F ranker (`fcrawl search --ranker bm25f`): title, meta (package, module,
  symbols, description) and body fields, a query-intent label boost and a named-package boost.
  `python -m focused_crawler.evaluate` reports P@5, MRR and nDCG@10 with paired randomization
  tests. On 48 *draft* judged queries (`config/eval_queries.yaml`, not yet reviewed), nDCG@10
  went from 0.557 (tf) to 0.905; the boosts alone were not significant vs. plain BM25F
  (`docs/ranking-results.md`). The default ranker is still `tf`.

### Changed
- Simulations seed peer keys (`Peer(key_seed=...)`), so a `--seed` reproduces node IDs, ownership
  and verdicts; this removes the flaky M3 simulation test. Results JSONs carry a `provenance`
  block (`src/runinfo`). `results/m3/host_rate*.json` and `docs/host-rate-results.md` were
  regenerated with seeded keys: the findings hold, magnitudes moved by up to about 20%.
- Docs refreshed for the canonical audits, host coordinators and BM25F ranker: README status,
  commands and headline results, development test counts (256) and layout, architecture notes on
  optional record/header fields. A flaky M3 simulation test is recorded in `docs/development.md`.

### Fixed
- Fetcher decodes pages as UTF-8 when the server declares no charset (titles were mojibake,
  e.g. `sys â System-specific...`).
- `crawl_ledger` hashes pages with Cloudflare per-response nonces removed, on both the crawl and
  audit side. Honest false FAILs went from 2/40 to 0/39 in the live run
  (`results/m3/live_run5.json`).

## [0.2.0] - 2026-10-26

### Added
- `crawl_ledger` package (M2/M3) imported from `../crawl-ledger`, with its 21 tests, seeds and
  results; lint fixes only, plus the seeds path now pointing at `config/ledger_seeds.txt`.
- `edge_serving` package (M4): served objects with Cache-Control rules, stateless verification
  gateway, edge proxy, client verifier, R1 revocation deltas on header gossip (optional
  `delta_root` in M3 headers), R2 Bloom-filter proof sharing, aiohttp servers with miss
  coalescing, and a trace-driven evaluation (`docs/m4-results.md`).

## [0.1.0] - 2026-10-26

### Added
- Project scaffold: packaging, lint/test tooling, pre-commit hooks, plans.
- YAML config loader, URL normalization/scope checks, polite fetcher (robots.txt,
  per-host delay, retries) and priority frontier with depth and per-host limits.
- HTML parser, rule-based COURSE/FACULTY/LAB/OTHER classifier with link scoring, and
  structured-field extractor with configurable CSS selectors.
- SQLite storage (pages, link graph, Courses/Faculty/Labs, postings) with duplicate
  detection, and a stemming tokenizer/indexer.
- Focused crawl loop, baseline TF-IDF ranker and `fcrawl` CLI (crawl/search/stats/export).
  Live smoke test on cc.gatech.edu: 30 pages, 88.5% academic yield, 0 errors.
