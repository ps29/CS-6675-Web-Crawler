# Plan: Academic-Domain Focused Crawler (implementation of M1 design)

## Context
The `Project/` folder is empty. The spec is `M1_Draft_AcademicFocusedCrawler.docx` (CS 6675, Fall 2026): a focused crawler for one university domain (default `gatech.edu`, configurable) that classifies pages as COURSE / FACULTY / LAB / OTHER, extracts structured fields, builds an inverted index plus a structured index, and ranks results. The baseline is a rule-based classifier with TF ranking. Refinement 1 adds a learned link/page classifier, and Refinement 2 adds metadata-aware ranking. The evaluation uses yield, coverage, P@5 and MRR.

User choices: **Python**, **single domain, deep crawl**, extract **links + metadata, structured text, custom selectors**.

Reuse: the polite fetching from `../crawl-ledger/crawl_ledger/fetcher.py` (robots.txt cache, per-host delay, User-Agent, `OfflineFetcher` pattern for tests). Copy and adapt it. Do not import across repos.

## Repository layout (`Project/`)
```
Project/
  .gitignore  .gitattributes  .editorconfig  .pre-commit-config.yaml
  pyproject.toml          # package metadata, deps, ruff + pytest config
  README.md  CHANGELOG.md
  config/default.yaml     # domain, seeds, budget, depth, delay, selectors, ranking weights
  docs/plans/             # plans live here (this plan copied as 00-overview.md, one file per milestone)
  docs/design/            # notes derived from M1 (architecture, data model)
  src/focused_crawler/
    config.py             # load/validate YAML -> dataclasses
    url_utils.py          # normalize (strip fragments/tracking/session params), in-scope check (*.domain)
    fetcher.py            # adapted from crawl-ledger: robots, per-host delay, HTML/PDF content-type filter, retries
    frontier.py           # priority queue (heapq) + visited set (hash of normalized URL), depth limit, per-host cap
    parser.py             # BeautifulSoup: title, meta, headings, main text, links with anchor text + context
    classifier.py         # RuleClassifier (URL paths + on-page cues, course-code regex) -> (label, score)
    extractor.py          # per-type fields + user-defined CSS selectors from config
    storage.py            # SQLite: pages, links (node/inlist/outlist), courses, faculty, labs, postings
    indexer.py            # tokenize + stem, write posting lists (term, doc_id, tf, positions)
    ranker.py             # TFRanker (baseline); MetadataRanker (R2)
    crawler.py            # main loop (M1 §3.4 pseudocode)
    cli.py                # `fcrawl crawl | search | stats | export`
  tests/                  # pytest, offline only (fixture HTML + FakeFetcher)
  data/                   # crawl outputs (gitignored)
```
Dependencies: `requests`, `beautifulsoup4`, `lxml`, `pyyaml`, `nltk` (Porter stemmer only), and later `scikit-learn`. Dev dependencies: `pytest`, `pytest-cov`, `ruff`, `pre-commit`.

## Milestones (each one gets a feature branch, merged into `main` with `--no-ff`)
1. **chore/scaffold**: run `git init -b main`, add `.gitignore` (venv, `__pycache__`, `data/`, `*.db`, `.env`), pyproject, ruff, pre-commit, README skeleton, and `docs/plans/`. Write the first commit.
2. **feat/fetch-frontier**: `config`, `url_utils`, `fetcher`, `frontier` with unit tests (normalization, scope, priority order, robots respect via the fake fetcher).
3. **feat/parse-classify-extract**: `parser`, `RuleClassifier`, `extractor` (including config CSS selectors) with fixture-HTML tests for each page type.
4. **feat/storage-index**: SQLite schema, `indexer` and an end-to-end offline crawl test over a fixture site graph.
5. **feat/crawler-cli**: the crawl loop, budget/depth/per-host limits, graceful error handling, `fcrawl crawl/search/stats/export (JSON/CSV)`. This is the baseline, done with a small live polite crawl (about 200 pages).
6. **feat/r1-learned-classifier**: label tool (`fcrawl label`), TF-IDF + logistic regression over page text and anchor text/context, with a fallback to rules below a confidence threshold.
7. **feat/r2-metadata-ranking**: the `w1*TF + w2*field_match + w3*recency` ranker, with the boost applied only when the query matches a field pattern.
8. **feat/evaluation**: `eval/` judged queries (30–50), yield/coverage/P@5/MRR scripts comparing baseline vs. R1 and R2, with results written to `docs/`.

## Git and coding practices
- Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`), small focused commits, and `main` kept green.
- Pre-commit hooks run ruff (lint + format) and the offline tests. Type hints throughout and short docstrings only where useful.
- No secrets in the repo. Crawl data, DBs and caches are gitignored. The commit trailer comes from the session.
- Plans go in `docs/plans/NN-<milestone>.md` and are updated as each milestone completes. `CHANGELOG.md` gets an entry per merge.
- No remote push unless you ask.

## Politeness and safety
Respect robots.txt, keep a delay of at least 1 s per host, use an identifying User-Agent, stay strictly in scope (`*.gatech.edu`), skip authentication-gated pages, and set hard page/depth budgets.

## Verification
- `pytest --cov` runs offline (no network) and passes on every milestone.
- `ruff check .` is clean and `pre-commit run --all-files` passes.
- Smoke test: `fcrawl crawl --config config/default.yaml --max-pages 200`, then `fcrawl stats` (yield by label), then `fcrawl search "CS 6675"` returns type-labeled results.
- `git log --oneline --graph` shows the scaffold commit and the merged milestone branches.

## First session scope
Milestones 1–5 (a working baseline). Milestones 6–8 follow in later sessions.
