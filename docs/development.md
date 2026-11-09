# Development

## Setup

Requires Python 3.11+.

```
python -m venv .venv
.venv\Scripts\activate            # Windows; use `source .venv/bin/activate` elsewhere
pip install -e ".[dev]"
pre-commit install
```

## Tests and linting

```
python -m pytest                  # 313 tests, fully offline
python -m pytest --cov            # with coverage
ruff check . && ruff format .
pre-commit run --all-files
```

Tests never touch the network: M1 uses fixture HTML and a fake fetcher, the ledger uses
`OfflineFetcher`, stub fetchers and mock origins, and M4 uses an injected clock
(`tests/edge/helpers.py`).

Simulations are deterministic for a given `--seed`: peers get a `key_seed`, so keys, node IDs and
package ownership repeat (`tests/ledger/test_determinism.py`). Before that, peer keys were random
and `test_m3.py::TestSim::test_all_forgeries_caught_without_collusion_or_drift` failed once in 20
full-suite runs; the exact failing case was not captured. Real peers still use random keys.

Every experiment that writes a results JSON adds a `provenance` block (`src/runinfo`): git commit,
`git_dirty`, Python version, platform, arguments and config. Commit code before generating results
that will be reported, so `git_dirty` is false.

| Directory | Covers |
|---|---|
| `tests/` | `focused_crawler` (15 modules, both profiles, BM25F ranker and evaluation metrics) |
| `tests/ledger/` | `crawl_ledger` baseline, M3 refinements, canonical block audits, host coordinators |
| `tests/edge/` | `edge_serving` objects, gateway, edge/client, R1, R2, R1-S, dishonest edges, HTTP, simulation |
| `tests/m5/` | `corpus_jobs` Streaming roles vs. the single node, MinHash, WET reader, corpus builder, estimate; real Hadoop only when `HADOOP_HOME` is set |
| `tests/gui/` | `lab_gui` forms, runner, task registry, and a window smoke test (skipped without a display) |

## Repository layout

```
config/            default.yaml (M1 Python-docs crawl), academic.yaml (M1 gatech.edu crawl),
                   eval_queries.yaml (M1 judged queries, draft), ledger_seeds.txt (M2/M3 seeds)
docs/              architecture, usage, findings, results, plans/, design/
results/           raw run outputs: m1/ (ranking evaluation), m3/ (live ledger runs, audit and
                   host-rate experiments), m4/ (serving simulation)
src/focused_crawler/   M1
src/crawl_ledger/      M2/M3
src/edge_serving/      M4
src/lab_gui/           Windows GUI that runs the experiments and tests
src/runinfo/          provenance block for results files
tests/             offline test suites
data/              crawl databases (gitignored)
```

## Git workflow

- `main` is always green. Work happens on `feat/*`, `fix/*` or `docs/*` branches, which are merged
  with `--no-ff`.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat(ledger): ...`, `docs: ...`, `test(edge): ...`).
- Every merge adds an entry to `CHANGELOG.md`. Releases are tagged (`v0.1.0`, `v0.2.0`).
- Plans go in `docs/plans/NN-<milestone>.md` and are updated when a milestone finishes.
- Crawl databases, `.env` files and caches are gitignored.

### Pre-commit pitfalls

- The `pytest` hook runs a bare `python`, so commit with the virtual environment active (or its
  `Scripts`/`bin` directory first on `PATH`). Otherwise collection fails with
  `ModuleNotFoundError`.
- The hook stashes unstaged changes but leaves untracked files in place. For a partial commit, run
  `git stash push --keep-index --include-untracked` first and `git stash pop` afterwards.
- `end-of-file-fixer` rewrites new JSON outputs. If a commit is blocked, restage the files and
  commit again.
- Keep the ruff version in `.pre-commit-config.yaml` equal to the dev dependency (`0.17.2`).

## Crawling politely

Both crawlers honor robots.txt, wait at least 1 s between requests to the same host and send an
identifying User-Agent. The M1 crawler stays inside the configured domain and enforces page, depth
and per-host budgets. The ledger crawler reads PyPI's documented JSON API, which robots.txt
disallows for generic crawlers but which is meant for programmatic use.
