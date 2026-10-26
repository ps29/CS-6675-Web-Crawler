# focused-crawler

Academic-domain focused crawler and ranking engine for university search, implementing the
CS 6675 M1 design "An Academic-Domain Focused Crawler and Ranking Method for University Search".

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
