# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
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
