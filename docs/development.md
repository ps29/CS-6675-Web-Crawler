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
python -m pytest                  # 138 tests, fully offline
python -m pytest --cov            # with coverage
ruff check . && ruff format .
pre-commit run --all-files
```

Tests never touch the network: M1 uses fixture HTML and a fake fetcher, the ledger uses
`OfflineFetcher`, and M4 uses an injected clock (`tests/edge/helpers.py`).

| Directory | Covers |
|---|---|
| `tests/` | `focused_crawler` (12 modules) |
| `tests/ledger/` | `crawl_ledger` baseline and M3 refinements |
| `tests/edge/` | `edge_serving` objects, gateway, edge/client, R1, R2, HTTP, simulation |

## Repository layout

```
config/            default.yaml (M1 crawler), ledger_seeds.txt (M2/M3 seed packages)
docs/              architecture, usage, findings, results, plans/, design/
results/           raw run outputs: m3/ (live ledger runs), m4/ (serving simulation)
src/focused_crawler/   M1
src/crawl_ledger/      M2/M3
src/edge_serving/      M4
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
