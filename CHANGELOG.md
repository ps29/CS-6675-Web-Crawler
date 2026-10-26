# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `crawl_ledger` package (M2/M3) imported from `../crawl-ledger`, with its 21 tests, seeds and
  results; lint fixes only, plus the seeds path now pointing at `config/ledger_seeds.txt`.

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
