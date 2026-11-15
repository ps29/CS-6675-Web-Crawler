"""Load a corpus job's output into an M1 search database, so the M1 rankers search it.

This is the link between the batch index job (M5) and the search engine (M1). The job reads a
corpus built from a crawl database (`corpus_jobs.corpus crawldb`) and writes `index.tsv` and
`pairs.tsv`; this module copies the crawl database (page metadata, labels and fields), replaces
its postings with the job's index, and can mark near-duplicate pages so the rankers skip them.

    python -m corpus_jobs.load --source-db data/pydocs.db --corpus data/project/pydocs.jsonl \
        --job-out data/project/out/local --out-db data/project/pydocs_m5.db --collapse-dups
    fcrawl --db data/project/pydocs_m5.db search "json dumps"

Notes:
- The job's index has term frequencies but no positions, so `postings.positions` is "[]". The
  TF-IDF and BM25F rankers use only tf and document length.
- A document's length is the sum of its term frequencies in the loaded index.
- `--collapse-dups` groups pages connected by near-duplicate pairs and keeps the page with the
  lowest id of each group (the one crawled first); the others get `duplicate_of`, which the
  rankers already honor for exact duplicates.
- Injected test copies (`<doc>~dup<i>`) have no page of their own and are skipped.
- `--check` compares the loaded postings with the source database's own (M1-built) postings.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path

from focused_crawler.storage import Storage

BATCH = 50_000


def doc_pages(corpus: Path, storage: Storage) -> tuple[dict[str, int], int]:
    """Map each corpus document to its page id by URL; count documents without a page."""
    by_url = {r["url"]: r["id"] for r in storage.conn.execute("SELECT id, url FROM pages")}
    mapping: dict[str, int] = {}
    skipped = 0
    with corpus.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if "~dup" in rec["doc"] or rec["url"] not in by_url:
                skipped += 1
            else:
                mapping[rec["doc"]] = by_url[rec["url"]]
    return mapping, skipped


def load_postings(index: Path, mapping: dict[str, int], storage: Storage) -> int:
    conn = storage.conn
    conn.execute("DELETE FROM postings")
    conn.execute("UPDATE pages SET doc_length = 0")
    lengths: dict[int, int] = defaultdict(int)
    rows: list[tuple[str, int, int]] = []
    n = 0

    def flush() -> None:
        conn.executemany(
            "INSERT INTO postings (term, page_id, tf, positions) VALUES (?, ?, ?, '[]')", rows
        )
        rows.clear()

    with index.open(encoding="utf-8") as f:
        for line in f:
            term, _df, postings = line.rstrip("\n").split("\t")
            for item in postings.split(","):
                doc, _, tf = item.rpartition(":")
                if doc in mapping:
                    rows.append((term, mapping[doc], int(tf)))
                    lengths[mapping[doc]] += int(tf)
                    n += 1
            if len(rows) >= BATCH:
                flush()
    flush()
    conn.executemany("UPDATE pages SET doc_length = ? WHERE id = ?",
                     ((length, pid) for pid, length in lengths.items()))  # fmt: skip
    return n


def collapse_duplicates(pairs: Path, mapping: dict[str, int], storage: Storage) -> int:
    """Mark all but the lowest-id page of each near-duplicate group; return how many."""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    with pairs.open(encoding="utf-8") as f:
        for line in f:
            a, b, _sim = line.rstrip("\n").split("\t")
            if a in mapping and b in mapping:
                ra, rb = find(mapping[a]), find(mapping[b])
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)  # the root is the group's lowest id
    marks = [(find(p), p) for p in parent if find(p) != p]
    storage.conn.executemany(
        "UPDATE pages SET duplicate_of = ? WHERE id = ? AND duplicate_of IS NULL", marks
    )
    return len(marks)


def check(source_db: Path, storage: Storage) -> dict:
    """Compare the loaded (term, page, tf) postings with the source database's own."""
    storage.conn.execute("ATTACH DATABASE ? AS src", (str(source_db),))
    q = storage.conn.execute
    same = q(
        """SELECT COUNT(*) FROM postings a JOIN src.postings b
           ON a.term = b.term AND a.page_id = b.page_id AND a.tf = b.tf"""
    ).fetchone()[0]
    result = {
        "source_postings": q("SELECT COUNT(*) FROM src.postings").fetchone()[0],
        "loaded_postings": q("SELECT COUNT(*) FROM postings").fetchone()[0],
        "identical": same,
    }
    storage.conn.commit()
    storage.conn.execute("DETACH DATABASE src")
    return result


def load(source_db: Path, corpus: Path, job_out: Path, out_db: Path, collapse: bool,
         verify: bool = False) -> dict:  # fmt: skip
    if out_db.resolve() == source_db.resolve():
        raise SystemExit("--out-db must differ from --source-db (the source is left untouched)")
    out_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_db, out_db)
    with Storage(out_db) as storage:
        mapping, skipped = doc_pages(corpus, storage)
        result = {"documents": len(mapping), "skipped_documents": skipped,
                  "postings": load_postings(job_out / "index.tsv", mapping, storage)}  # fmt: skip
        if collapse:
            result["collapsed_pages"] = collapse_duplicates(job_out / "pairs.tsv", mapping, storage)
        if verify:
            result["check"] = check(source_db, storage)
        result["indexed_pages"] = storage.indexed_count()
    return result


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m corpus_jobs.load", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument(
        "--source-db", default="data/pydocs.db", help="M1 crawl database the corpus came from"
    )
    ap.add_argument(
        "--corpus",
        default="data/project/pydocs.jsonl",
        help="corpus .jsonl built with `corpus crawldb`",
    )
    ap.add_argument(
        "--job-out", default="data/project/out/local", help="output directory of corpus_jobs.run"
    )
    ap.add_argument(
        "--out-db", default="data/project/pydocs_m5.db", help="search database to write"
    )
    ap.add_argument("--collapse-dups", action="store_true", help="hide near-duplicate pages")
    ap.add_argument("--check", action="store_true", help="compare with the source postings")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    result = load(Path(a.source_db), Path(a.corpus), Path(a.job_out), Path(a.out_db),
                  a.collapse_dups, a.check)  # fmt: skip
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
