# focused-crawler

CS 6675 course project. Three packages under `src/`:

| Package | Milestone | Status |
|---|---|---|
| `focused_crawler` | M1: academic-domain focused crawler and ranking | baseline done (`v0.1.0`) |
| `crawl_ledger` | M2/M3: decentralized Python-docs crawler with signed records, Merkle blocks, audits | imported from `../crawl-ledger` |
| `edge_serving` | M4: edge caching and revocation for verified crawl records | in progress (`docs/plans/01-m4-serving-layer.md`) |

## crawl_ledger (M2/M3)

```
python -m crawl_ledger.sim --offline
python -m crawl_ledger.sim_m3 --compare --peers 100 --dishonest 0.2 --forge-rate 0.05
```

Seed packages are in `config/ledger_seeds.txt`; earlier results are in `results/m3/`; the original
README is `docs/design/crawl-ledger-README.md`.

## focused_crawler (M1)

Academic-domain focused crawler and ranking engine for university search, implementing the
M1 design "An Academic-Domain Focused Crawler and Ranking Method for University Search".

The crawler stays inside one institutional domain (default `gatech.edu`), classifies each page as
`COURSE`, `FACULTY`, `LAB` or `OTHER`, prioritizes the frontier toward academic pages, extracts
structured fields, and serves ranked, type-labeled search results from a SQLite index.

## Setup

```
python -m venv .venv
.venv\Scripts\activate          # Windows  (source .venv/bin/activate elsewhere)
pip install -e ".[dev]"
pre-commit install
```

## Usage

```
fcrawl crawl --config config/default.yaml --max-pages 200
fcrawl stats
fcrawl search "CS 6675"
fcrawl export --format json --out data/pages.json
```

## Development

- Tests run offline: `python -m pytest`
- Lint/format: `ruff check . && ruff format .`
- Plans live in `docs/plans/`; see `docs/plans/00-overview.md`.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/); work happens on
  `feat/*` / `fix/*` branches merged into `main` with `--no-ff`.

## Politeness

The crawler honors robots.txt, waits at least 1 s between requests to the same host, sends an
identifying User-Agent, stays strictly within the configured domain, and enforces page and depth
budgets.
