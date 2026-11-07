# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

CS 6675 course project: three Python packages under `src/` (Python 3.11+). They share tooling, not code,
except that `edge_serving` builds on `crawl_ledger`; `focused_crawler` is independent.

- `focused_crawler` (M1): profile-driven focused crawler + search engine. CLI: `fcrawl`.
- `crawl_ledger` (M2/M3): decentralized Python-docs crawler; peers on a Chord ring publish Ed25519-signed, hash-chained records, committed in Merkle blocks and audited by re-fetching.
- `edge_serving` (M4): edge caching of verified ledger records, with R1 (revocation deltas) and R2 (Bloom-filter proof sharing) refinements.

Fuller detail lives in `docs/architecture.md` (components, protocols), `docs/usage.md` (every command/config key) and `docs/development.md`. Read those rather than re-deriving.

## Commands

Windows dev environment; activate the venv first (`.venv\Scripts\activate`). The pre-commit `pytest` hook runs a bare `python`, so commit with the venv active or it fails with `ModuleNotFoundError`.

```
pip install -e ".[dev]" && pre-commit install
python -m pytest                                   # all tests, fully offline
python -m pytest tests/test_crawler.py::test_name  # single test
python -m pytest tests/edge -k revocation          # one suite / keyword
python -m pytest --cov
ruff check . && ruff format .
pre-commit run --all-files
```

Running the systems:

```
fcrawl crawl --max-pages 500            # default profile python_docs -> data/pydocs.db
fcrawl --config config/academic.yaml crawl --max-pages 200   # academic profile -> data/crawl.db
fcrawl stats
fcrawl search "json.dumps"
python -m crawl_ledger.sim --peers 20 --dishonest 0.2 --forge-rate 0.2   # live; add --offline for no network
python -m edge_serving.sim --compare
python -m edge_serving.http_app demo
```

## Architecture notes

**focused_crawler**: a *profile* (`profiles.py`) supplies the classifier, link scorer and per-label extractors (`python_docs.py` for the default profile; `classifier.py`/`extractor.py` for `academic`). Fetcher, frontier (heap priority queue), parser, storage (SQLite), indexer (Porter-stemmed postings) and ranker (TF-IDF) are shared. Labels are unique across profiles so one SQLite schema holds both crawl kinds. Each page is handled in its own `try` so one bad page never stops the crawl. Config is YAML (`config/default.yaml`, `config/academic.yaml`) loaded and validated in `config.py`.

**crawl_ledger**: `record.py`/`peer.py` build and sign records and keep the per-peer hash-chained log; `ring.py` assigns package ownership (successor of hash(name)); `verify.py` checks IDs/signatures/chains/Merkle branches; `merkle.py`/`blocks.py` give block headers and a `HeaderChain` with finality; `audit.py` returns `PASS` / `INCONCLUSIVE` / `FAIL`; `r2.py` does auditor selection and reputation. `sim.py` is the live/offline baseline run, `sim_m3.py` the synthetic baseline-vs-R1-vs-R2 comparison. Dishonest peers' forged flag is ground truth only, for measuring detection.

**edge_serving**: serves three object types (immutable `Proof`, TTL'd `Status`, `Pointer`) via a stateless `gateway.py` signing statuses, `edge.py` caches, and `client.py` verifiers (IDE and RAG policies differ in accepted statuses and `max_lag`). `ledger_view.py` seals epochs and builds the delta; `delta.py`/`revocation.py` implement R1, `summary.py` R2. `sim.py` is a trace-driven simulation with an injected clock.

**Cross-package invariant**: the ledger `Header` has an optional `delta_root` that enters the header ID only when set, so M3 header IDs and tests are unchanged by M4. Preserve this when touching `blocks.py`.

## Testing conventions

Tests never touch the network: M1 uses fixture HTML (`tests/fixtures/`) and fake fetchers (`tests/fakes.py`), the ledger uses `OfflineFetcher`, and M4 uses an injected clock (`tests/edge/helpers.py`). Layout: `tests/` (focused_crawler), `tests/ledger/`, `tests/edge/`.

## Style and git workflow

- Ruff, line length 100, rules `E,F,W,I,B,UP,SIM`. The ruff version in `.pre-commit-config.yaml` must equal the dev dependency in `pyproject.toml` (`0.17.2`).
- Work on `feat/*`, `fix/*` or `docs/*` branches, merged to `main` with `--no-ff`; `main` stays green. Conventional Commits (`feat(ledger): ...`). Each merge adds a `CHANGELOG.md` entry.
- Pre-commit stashes unstaged changes but leaves untracked files; for a partial commit run `git stash push --keep-index --include-untracked` first and `git stash pop` after. If `end-of-file-fixer` rewrites new JSON and blocks the commit, restage and commit again.
- `data/` (crawl DBs), `.env` and caches are gitignored; raw run outputs go in `results/m3/` and `results/m4/`.

## Politeness

Crawlers honor robots.txt, wait at least 1 s per host and send an identifying User-Agent. The ledger crawler reads PyPI's JSON API, which robots.txt disallows for generic crawlers but is intended for programmatic use.
