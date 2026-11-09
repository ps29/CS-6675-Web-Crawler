"""Single-node baseline: the same three jobs in one Python process, all state in memory."""

from __future__ import annotations

import heapq
from collections import Counter, defaultdict
from pathlib import Path

from .core import MAX_BUCKET, bands, bucket_pairs, pair_line, parse, postings_line, signature
from .core import tokens as tokenize
from .streaming import TOP_K


def run(corpus: Path, out: Path, jobs: tuple[str, ...]) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    index: dict[str, list[tuple[str, int]]] = defaultdict(list)
    counts: Counter = Counter()
    buckets: dict[str, list] = defaultdict(list)
    with corpus.open(encoding="utf-8") as f:
        for line in f:
            doc, text = parse(line)
            toks = tokenize(text)
            tf = Counter(toks)
            if "index" in jobs:
                for t, c in tf.items():
                    index[t].append((doc, c))
            if "topk" in jobs:
                counts.update(tf)
            if "dedup" in jobs and (sig := signature(toks)) is not None:
                for b in bands(sig):
                    buckets[b].append((doc, sig))
    stats = {}
    if "index" in jobs:
        with (out / "index.tsv").open("w", encoding="utf-8") as w:
            for term in sorted(index):
                w.write(postings_line(term, index[term]) + "\n")
    if "topk" in jobs:
        best = heapq.nsmallest(TOP_K, counts.items(), key=lambda tc: (-tc[1], tc[0]))
        (out / "topk.tsv").write_text("".join(f"{t}\t{c}\n" for t, c in best), encoding="utf-8")
    if "dedup" in jobs:
        pairs: dict[tuple[str, str], float] = {}
        skipped = 0
        for members in buckets.values():
            if len(members) > MAX_BUCKET:
                skipped += 1
                continue
            for a, b, s in bucket_pairs(members):
                pairs[(a, b)] = max(s, pairs.get((a, b), 0.0))
        lines = sorted(pair_line(a, b, s) for (a, b), s in pairs.items())
        (out / "pairs.tsv").write_text("".join(x + "\n" for x in lines), encoding="utf-8")
        stats["skipped_buckets"] = skipped
    return stats
