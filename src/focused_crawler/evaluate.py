"""Ranking evaluation: P@5, MRR and nDCG@10 over judged queries, with paired tests.

    python -m focused_crawler.evaluate --db data/pydocs.db --queries config/eval_queries.yaml \
        --out results/m1/ranking_eval.json

Configurations: `tf` (the M1 baseline), `bm25` (BM25 on the body only), `bm25f` (title, meta
and body fields), and bm25f with the query-intent boost, the named-package boost, or both.
Each configuration is compared with `tf` by a paired randomization (sign-flip) test on the
per-query scores. Parameters are fixed in advance (`BM25FParams` defaults), not tuned on the
queries."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from pathlib import Path

import yaml

from runinfo import provenance

from .ranker import BM25FParams, BM25FRanker, TFRanker, query_intent
from .storage import Storage

FIELDS_ONLY = dict(intent_boost=0.0, package_boost=0.0)
CONFIGS = {
    "tf": None,
    "bm25": BM25FParams(weights=(("title", 0.0), ("meta", 0.0), ("body", 1.0)), **FIELDS_ONLY),
    "bm25f": BM25FParams(**FIELDS_ONLY),
    "bm25f+intent": BM25FParams(package_boost=0.0),
    "bm25f+package": BM25FParams(intent_boost=0.0),
    "bm25f+intent+package": BM25FParams(),
}
METRICS = ("p@5", "rr", "ndcg@10")


def load_queries(path: Path) -> tuple[str, list[dict]]:
    data = yaml.safe_load(path.read_text())
    return data.get("status", "unknown"), data["queries"]


def metrics(ranked: list[str], rel: dict[str, int]) -> dict[str, float]:
    p5 = sum(rel.get(u, 0) > 0 for u in ranked[:5]) / 5
    rr = next((1 / i for i, u in enumerate(ranked, 1) if rel.get(u, 0) > 0), 0.0)
    dcg = sum((2 ** rel.get(u, 0) - 1) / math.log2(i + 1) for i, u in enumerate(ranked[:10], 1))
    ideal = sorted(rel.values(), reverse=True)[:10]
    idcg = sum((2**g - 1) / math.log2(i + 1) for i, g in enumerate(ideal, 1))
    return {"p@5": p5, "rr": rr, "ndcg@10": dcg / idcg if idcg else 0.0}


def paired_test(a: list[float], b: list[float], iters: int = 10000, seed: int = 0) -> float:
    """Two-sided p-value that mean(b - a) = 0, by randomly flipping the sign of differences."""
    diffs = [y - x for x, y in zip(a, b, strict=True)]
    observed = abs(statistics.mean(diffs))
    if observed == 0:
        return 1.0
    rng = random.Random(seed)
    hits = sum(
        abs(statistics.mean(d if rng.random() < 0.5 else -d for d in diffs)) >= observed - 1e-12
        for _ in range(iters)
    )
    return (hits + 1) / (iters + 1)


def evaluate(storage: Storage, queries: list[dict], k: int = 10) -> dict:
    known = {r["url"] for r in storage.conn.execute("SELECT url FROM pages")}
    missing = sorted({u for q in queries for u in q["rel"] if u not in known})
    if missing:
        raise SystemExit(f"judged URLs not in the index: {missing}")
    rankers = {name: TFRanker(storage) if p is None else BM25FRanker(storage, p)
               for name, p in CONFIGS.items()}  # fmt: skip
    per_query = []
    for q in queries:
        row = {"q": q["q"], "intent": sorted(query_intent(q["q"])) or ["NONE"]}
        for name, ranker in rankers.items():
            ranked = [r.url for r in ranker.search(q["q"], k=k)]
            row[name] = metrics(ranked, q["rel"])
            row[name]["top"] = ranked[:3]
        per_query.append(row)
    summary = {}
    for name in CONFIGS:
        summary[name] = {m: round(statistics.mean(r[name][m] for r in per_query), 3)
                         for m in METRICS}  # fmt: skip
        for base in ("tf", "bm25f"):
            if name == base or (base == "bm25f" and not name.startswith("bm25f+")):
                continue
            for m in ("rr", "ndcg@10"):
                summary[name][f"p_vs_{base}_{m}"] = round(
                    paired_test([r[base][m] for r in per_query], [r[name][m] for r in per_query]),
                    4,
                )
    groups: dict[str, list[dict]] = {}
    for r in per_query:
        groups.setdefault("+".join(r["intent"]), []).append(r)
    by_intent = {
        g: {
            "n": len(rs),
            **{name: round(statistics.mean(r[name]["ndcg@10"] for r in rs), 3) for name in CONFIGS},
        }  # fmt: skip
        for g, rs in sorted(groups.items())
    }
    best_p5 = statistics.mean(min(5, sum(g > 0 for g in q["rel"].values())) / 5 for q in queries)
    return {"queries": len(per_query), "max_p@5": round(best_p5, 3), "summary": summary,
            "ndcg_by_intent": by_intent, "per_query": per_query}  # fmt: skip


def print_report(result: dict, status: str) -> None:
    print(f"{result['queries']} queries (judgments: {status}); "
          f"highest possible P@5 {result['max_p@5']}")  # fmt: skip
    print("config".ljust(22) + "".join(m.rjust(9) for m in METRICS)
          + "  vs tf: p(rr) p(ndcg)   vs bm25f: p(rr) p(ndcg)")  # fmt: skip
    for name, s in result["summary"].items():
        ps = "  ".join(f"{s.get(f'p_vs_{b}_{m}', '-'):>7}" for b in ("tf", "bm25f")
                       for m in ("rr", "ndcg@10"))  # fmt: skip
        print(name.ljust(22) + "".join(f"{s[m]:9.3f}" for m in METRICS) + "  " + ps)
    print("\nnDCG@10 by detected query intent")
    for g, row in result["ndcg_by_intent"].items():
        cells = "  ".join(f"{name}={row[name]:.3f}" for name in CONFIGS)
        print(f"  {g} (n={row['n']}): {cells}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m focused_crawler.evaluate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("--db", default="data/pydocs.db")
    ap.add_argument("--queries", default="config/eval_queries.yaml")
    ap.add_argument("--out", default="")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    status, queries = load_queries(Path(a.queries))
    with Storage(a.db) as storage:
        result = evaluate(storage, queries)
    result["judgments"] = status
    result = {"provenance": provenance(vars(a)), **result}
    print_report(result, status)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
