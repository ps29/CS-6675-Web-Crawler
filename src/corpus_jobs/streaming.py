"""Hadoop Streaming mappers and reducers: `python -m corpus_jobs.streaming <role>`.

Each role reads stdin and writes tab-separated lines to stdout. Hadoop sorts mapper output by
key (the text before the first tab) and gives each reducer its keys in order, but the values of
one key arrive in no particular order, so every reducer is order-insensitive.

Baseline roles emit one record per token occurrence and rely on the shuffle to aggregate.
Refined roles aggregate before the shuffle: per document (in-mapper combining, Lin and Dyer
2010), per map task (a bounded dictionary flushed when full), and with a combiner.

Hadoop counters are reported through stderr (`reporter:counter:group,name,amount`)."""

from __future__ import annotations

import heapq
import sys
from collections import Counter
from itertools import groupby

from .core import (
    MAX_BUCKET,
    bands,
    bucket_pairs,
    pair_line,
    parse,
    postings_line,
    sig_from_str,
    sig_to_str,
    signature,
    tokens,
)

TOP_K = 1000
FLUSH_AT = 200_000  # distinct keys held by an in-mapper aggregator before it is flushed


def counter(name: str, amount: int = 1) -> None:
    sys.stderr.write(f"reporter:counter:corpus_jobs,{name},{amount}\n")


def keyed(lines):
    """(key, [rest, ...]) groups of a sorted stream of tab-separated lines."""
    rows = (line.rstrip("\n").split("\t", 1) for line in lines if line.strip())
    for key, group in groupby(rows, key=lambda r: r[0]):
        yield key, [r[1] if len(r) > 1 else "" for r in group]


# --- inverted index ------------------------------------------------------------------------


def index_map_naive(stdin, out):
    for line in stdin:
        doc, text = parse(line)
        for t in tokens(text):
            out.write(f"{t}\t{doc}\n")


def index_reduce_naive(stdin, out):
    for term, docs in keyed(stdin):
        out.write(postings_line(term, list(Counter(docs).items())) + "\n")


def index_map(stdin, out):
    for line in stdin:
        doc, text = parse(line)
        for t, tf in Counter(tokens(text)).items():
            out.write(f"{t}\t{doc}:{tf}\n")


def index_reduce(stdin, out):
    for term, vals in keyed(stdin):
        ps = [(d, int(tf)) for d, tf in (v.rsplit(":", 1) for v in vals)]
        out.write(postings_line(term, ps) + "\n")


# --- term counts and top-k -----------------------------------------------------------------


def wc_map_naive(stdin, out):
    for line in stdin:
        _, text = parse(line)
        for t in tokens(text):
            out.write(f"{t}\t1\n")


def wc_map(stdin, out):
    counts: Counter = Counter()
    for line in stdin:
        _, text = parse(line)
        counts.update(tokens(text))
        if len(counts) >= FLUSH_AT:
            _flush(counts, out)
    _flush(counts, out)


def _flush(counts: Counter, out) -> None:
    for t, c in counts.items():
        out.write(f"{t}\t{c}\n")
    counts.clear()


def wc_reduce(stdin, out):  # also the combiner
    for term, vals in keyed(stdin):
        out.write(f"{term}\t{sum(int(v) for v in vals)}\n")


def topk_map_naive(stdin, out):  # identity: every term goes to the single reducer
    for line in stdin:
        out.write(f"top\t{line.rstrip(chr(10))}\n")


def topk_map(stdin, out):  # local top-k: at most TOP_K records leave each map task
    best = heapq.nsmallest(TOP_K, _term_counts(stdin), key=lambda tc: (-tc[1], tc[0]))
    for t, c in best:
        out.write(f"top\t{t}\t{c}\n")


def _term_counts(stdin):
    for line in stdin:
        t, c = line.rstrip("\n").split("\t")
        yield t, int(c)


def topk_reduce(stdin, out):
    rows = []
    for _, vals in keyed(stdin):
        rows.extend((t, int(c)) for t, c in (v.split("\t") for v in vals))
    for t, c in heapq.nsmallest(TOP_K, rows, key=lambda tc: (-tc[1], tc[0])):
        out.write(f"{t}\t{c}\n")


# --- near-duplicates (MinHash + LSH) -------------------------------------------------------


def dedup_map(stdin, out):
    for line in stdin:
        doc, text = parse(line)
        sig = signature(tokens(text))
        if sig is None:
            continue
        s = sig_to_str(sig)
        for b in bands(sig):
            out.write(f"{b}\t{doc}\t{s}\n")


def dedup_reduce(stdin, out):
    for _, vals in keyed(stdin):
        if len(vals) > MAX_BUCKET:
            counter("skipped_buckets")
            continue
        members = [(d, sig_from_str(s)) for d, s in (v.split("\t") for v in vals)]
        for a, b, sim in bucket_pairs(members):
            out.write(f"{a}|{b}\t{sim:.4f}\n")


def pairs_reduce(stdin, out):  # a pair found in several bands is kept once
    for key, sims in keyed(stdin):
        a, b = key.split("|")
        out.write(pair_line(a, b, max(float(s) for s in sims)) + "\n")


ROLES = {f.__name__: f for f in (
    index_map_naive, index_reduce_naive, index_map, index_reduce, wc_map_naive, wc_map,
    wc_reduce, topk_map_naive, topk_map, topk_reduce, dedup_map, dedup_reduce, pairs_reduce,
)}  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    role = (argv or sys.argv[1:])[0]
    ROLES[role](sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
