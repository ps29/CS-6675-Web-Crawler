"""Command-line interface: fcrawl crawl | search | stats | export."""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import logging
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .crawler import Crawler
from .profiles import ALL_LABELS
from .ranker import TFRanker
from .storage import Storage

DEFAULT_CONFIG = Path("config/default.yaml")
EXPORT_COLUMNS = ("id", "url", "label", "label_score", "title", "depth", "status", "fields")
HIDDEN_FIELDS = {"course_codes", "symbols", "sections", "versions"}  # long lists


def _db_path(args) -> Path:
    if args.db:
        return Path(args.db)
    return load_config(args.config).db_path


def cmd_crawl(args) -> int:
    config = load_config(args.config)
    db_path = Path(args.db) if args.db else config.db_path
    with Storage(db_path) as storage:
        crawler = Crawler(config, storage)
        try:
            stats = crawler.run(max_pages=args.max_pages)
        except KeyboardInterrupt:
            stats = crawler.stats
            print("interrupted; partial results saved", file=sys.stderr)
    print(
        f"fetched {stats.fetched} pages ({stats.html_pages} HTML), "
        f"yield {stats.useful_yield:.1%}, labels {dict(stats.labels)}, "
        f"skipped {stats.blocked_or_failed}, errors {stats.errors} -> {db_path}"
    )
    return 0


def cmd_search(args) -> int:
    with Storage(_db_path(args)) as storage:
        results = TFRanker(storage).search(args.query, k=args.k, label=args.label)
    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
        return 0
    if not results:
        print("no results")
    for rank, r in enumerate(results, 1):
        print(f"{rank:2}. [{r.label:<9}] {r.score:6.3f}  {r.title[:70]}")
        print(f"    {r.url}")
        shown = {k: v for k, v in r.fields.items() if v and k not in HIDDEN_FIELDS}
        if shown:
            print(f"    {json.dumps(shown)[:160]}")
    return 0


def cmd_stats(args) -> int:
    with Storage(_db_path(args)) as storage:
        print(json.dumps(storage.summary(), indent=2))
    return 0


def cmd_export(args) -> int:
    with Storage(_db_path(args)) as storage:
        pages = list(storage.iter_pages())
    with (
        open(args.out, "w", encoding="utf-8", newline="")
        if args.out
        else contextlib.nullcontext(sys.stdout)
    ) as out:
        if args.format == "json":
            json.dump(pages, out, indent=2)
        else:
            writer = csv.DictWriter(out, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for page in pages:
                writer.writerow({**page, "fields": json.dumps(page["fields"])})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fcrawl", description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="YAML config path")
    parser.add_argument("--db", help="SQLite path (overrides config)")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every page")
    sub = parser.add_subparsers(dest="command", required=True)

    crawl = sub.add_parser("crawl", help="run a focused crawl")
    crawl.add_argument("--max-pages", type=int, help="override crawl.max_pages")
    crawl.set_defaults(func=cmd_crawl)

    search = sub.add_parser("search", help="query the index")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=10)
    search.add_argument("--label", choices=ALL_LABELS)
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=cmd_search)

    stats = sub.add_parser("stats", help="summarize the crawl database")
    stats.set_defaults(func=cmd_stats)

    export = sub.add_parser("export", help="export pages and fields")
    export.add_argument("--format", choices=("json", "csv"), default="json")
    export.add_argument("--out", help="output file (default: stdout)")
    export.set_defaults(func=cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
