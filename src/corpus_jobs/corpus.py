"""Build corpus files: one JSON line per document, {"doc", "url", "text"}.

Sources:
- Common Crawl WET files (`WARC-Type: conversion` records, the extracted plain text);
- the M3 drift collection (`data/drift/`): HTML pages reduced to their text, PyPI JSON skipped;
- an M1 crawl database (`crawldb`, e.g. `data/pydocs.db`). The crawler does not keep page text,
  only positional postings, so each page's text is rebuilt as its stemmed token stream in
  position order. An index built from this corpus therefore has the M1 indexer's terms, and
  `corpus_jobs.load` can hand it back to the M1 rankers. Terms longer than the job tokenizer's
  40-character limit (hashes, long concatenated dotted names) are left out, because the
  tokenizer would otherwise cut them into pieces that the M1 index does not contain.

`--inject-dups N` appends N near-duplicate copies of randomly chosen documents, each with a
share of its
words replaced (drawn from `--edit-rates`), so near-duplicate detection has a known ground truth: a
pair is a true near-duplicate when its exact shingle Jaccard is at least core.THRESHOLD. Copies
are named `<doc>~dup<i>` and listed in `<out>.truth.json`.

    python -m corpus_jobs.corpus wet data/cc/wet0.warc.wet.gz --out data/m5/average.jsonl
    python -m corpus_jobs.corpus drift data/drift --out data/m5/easy.jsonl
    python -m corpus_jobs.corpus crawldb data/pydocs.db --out data/project/pydocs.jsonl \
        --min-tokens 1
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from .core import TOKEN, exact_jaccard, tokens

MAX_TOKEN = 40  # upper bound of core.TOKEN


def wet_records(path: Path) -> Iterator[tuple[str, str]]:
    """(url, text) for each conversion record of a WET file (WARC/1.0 framing)."""
    with gzip.open(path, "rb") as f:
        while True:
            line = f.readline()
            if not line:
                return
            if not line.startswith(b"WARC/"):
                continue
            headers = {}
            while (h := f.readline().rstrip(b"\r\n")) != b"":
                k, _, v = h.partition(b":")
                headers[k.strip().lower()] = v.strip()
            body = f.read(int(headers.get(b"content-length", b"0")))
            if headers.get(b"warc-type") == b"conversion":
                url = headers.get(b"warc-target-uri", b"").decode("utf-8", "replace")
                yield url, body.decode("utf-8", "replace")


def drift_records(root: Path) -> Iterator[tuple[str, str]]:
    from bs4 import BeautifulSoup

    from crawl_ledger.audit_eval import _key

    index = json.loads((root / "index.json").read_text())
    for t in index["targets"]:
        f = root / "p0" / f"{_key(t['url'])}.bin"
        if not f.exists():
            continue
        content = f.read_bytes()
        if content.lstrip()[:1] in (b"{", b"["):
            continue  # PyPI JSON metadata, not prose
        soup = BeautifulSoup(content, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        yield t["url"], soup.get_text(" ")


def crawldb_records(path: Path) -> Iterator[tuple[str, str]]:
    """(url, token stream) for each indexed page of an M1 crawl database, in page-id order."""
    # as_uri() escapes "#", "?" and "%", which would otherwise end the file name in the URI
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        pages = conn.execute(
            "SELECT id, url FROM pages WHERE doc_length > 0 ORDER BY id"
        ).fetchall()
        for page_id, url in pages:
            stream: dict[int, str] = {}
            rows = conn.execute("SELECT term, positions FROM postings WHERE page_id = ?",
                                (page_id,))  # fmt: skip
            for term, positions in rows:
                if len(term) <= MAX_TOKEN:
                    for pos in json.loads(positions):
                        stream[pos] = term
            yield url, " ".join(stream[i] for i in sorted(stream))
    finally:
        conn.close()


def perturb(text: str, rate: float, rng: random.Random) -> str:
    """Replace a share `rate` of the words with random ones (a near-duplicate copy)."""
    words = text.split()
    for i in range(len(words)):
        if rng.random() < rate:
            words[i] = f"x{rng.randrange(10**6)}"
    return " ".join(words)


def build(records, out: Path, inject: int, rates: list[float], seed: int, min_tokens: int) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    docs: list[tuple[str, str, str]] = []
    for i, (url, text) in enumerate(records):
        if len(TOKEN.findall(text)) >= min_tokens:
            docs.append((f"d{i:07d}", url, text))
    truth = []
    for j, (doc, url, text) in enumerate(rng.sample(docs, min(inject, len(docs)))):
        rate = rng.choice(rates)
        copy = perturb(text, rate, rng)
        name = f"{doc}~dup{j}"
        docs.append((name, url, copy))
        jac = round(exact_jaccard(tokens(text), tokens(copy)), 4)
        truth.append({"a": doc, "b": name, "edit_rate": rate, "jaccard": jac})
    with out.open("w", encoding="utf-8") as f:
        for doc, url, text in docs:
            f.write(json.dumps({"doc": doc, "url": url, "text": text}, ensure_ascii=False) + "\n")
    meta = {"docs": len(docs), "injected": len(truth), "edit_rates": rates, "seed": seed,
            "bytes": out.stat().st_size, "truth": truth}  # fmt: skip
    Path(f"{out}.truth.json").write_text(json.dumps(meta, indent=1))
    return meta


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m corpus_jobs.corpus", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("source", choices=("wet", "drift", "crawldb"))
    ap.add_argument("paths", nargs="+", help="WET files, the drift directory, or a crawl database")
    ap.add_argument("--out", required=True)
    ap.add_argument("--inject-dups", type=int, default=0)
    ap.add_argument("--edit-rates", type=float, nargs="+", default=[0.005, 0.01, 0.02, 0.05])
    ap.add_argument("--min-tokens", type=int, default=20, help="skip shorter documents")
    ap.add_argument("--seed", type=int, default=1)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.source == "wet":
        records = (r for p in a.paths for r in wet_records(Path(p)))
    elif a.source == "crawldb":
        records = crawldb_records(Path(a.paths[0]))
    else:
        records = drift_records(Path(a.paths[0]))
    meta = build(records, Path(a.out), a.inject_dups, a.edit_rates, a.seed, a.min_tokens)
    print(f"{meta['docs']} docs ({meta['injected']} injected near-duplicates), "
          f"{meta['bytes'] / 1e6:.1f} MB -> {a.out}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
