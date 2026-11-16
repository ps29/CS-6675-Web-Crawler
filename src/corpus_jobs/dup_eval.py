"""Does collapsing near-duplicate pages improve the ranked results? A controlled experiment.

    python -m corpus_jobs.dup_eval --seeds 10 --out results/project/dup_eval.json

The 149-page Python-docs crawl has only one near-duplicate pair, too few to measure anything.
Documentation sites often serve the same page under several paths (a version or "latest" next to
"stable"), so this experiment adds such mirrors on purpose and measures what collapsing does:

1. Every indexed page of the crawl database gets a mirror with probability `--mirror-rate`. The
   mirror is a new page on the same host under `/mirror/`, with a small share of its words
   replaced (0.5%, 1% or 2%, chosen at random), and the original's title, label and fields.
2. The index job (single node; every engine gives the same output) builds the index and the
   near-duplicate pairs of the crawl plus its mirrors.
3. The loader writes two search databases from the same job output: one as it is, and one with
   near-duplicates collapsed (`corpus_jobs.load`, `--collapse-dups`).
4. The 48 judged queries (`config/eval_queries.yaml`) are run on both, and scored two ways:
   - **plain**: a mirror is as relevant as its original (it shows the same content);
   - **group-aware**: a page and its mirror count as one result, so a result whose original or
     mirror already appeared higher in the list earns nothing. This is the view of a user, who
     gains nothing from reading the same page twice.
   `redundant@10` is how many of the top 10 results repeat a page shown higher.

Ground truth is the list of mirrors made in step 1; the near-duplicate detector does not see it.
Seeds 1..N give N independently mirrored collections; per query, the scores are averaged over
the seeds, and the two databases are compared with the paired randomization test of
`focused_crawler.evaluate` over the queries.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sqlite3
import statistics
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from focused_crawler.evaluate import CONFIGS, load_queries, metrics, paired_test
from focused_crawler.ranker import BM25FRanker, TFRanker
from focused_crawler.storage import Storage
from runinfo import provenance

from . import load, local
from .corpus import build, crawldb_records, perturb

RANKERS = ("tf", "bm25f+intent+package")
RATES = (0.005, 0.01, 0.02)
DUP = "__repeat__"  # stands in for a result that repeats a page shown higher


def mirror_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "/mirror" + (p.path or "/"), p.query, ""))


def add_mirror_pages(src_db: Path, mirrors: dict[str, str]) -> None:
    """Copy each original page row (and its doc_pages row) under the mirror URL."""
    conn = sqlite3.connect(src_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pages)") if r[1] != "id"]
    copy = [c if c not in ("url", "content_hash", "duplicate_of") else "?" for c in cols]
    for original, mirror in mirrors.items():
        (pid,) = conn.execute("SELECT id FROM pages WHERE url = ?", (original,)).fetchone()
        replaced = ("url", "content_hash", "duplicate_of")
        params = [mirror if c == "url" else None for c in cols if c in replaced]
        sql = f"INSERT INTO pages ({', '.join(cols)}) SELECT {', '.join(copy)} FROM pages "
        conn.execute(sql + "WHERE id = ?", [*params, pid])
        new = conn.execute("SELECT id FROM pages WHERE url = ?", (mirror,)).fetchone()[0]
        dcols = [r[1] for r in conn.execute("PRAGMA table_info(doc_pages)") if r[1] != "page_id"]
        conn.execute(
            f"INSERT INTO doc_pages (page_id, {', '.join(dcols)}) "
            f"SELECT ?, {', '.join(dcols)} FROM doc_pages WHERE page_id = ?",
            (new, pid),
        )
    conn.commit()
    conn.close()


def score(ranked: list[str], rel: dict[str, int], canon: dict[str, str]) -> dict:
    """Plain metrics (a mirror inherits its original's grade) and group-aware metrics."""
    plain_rel = {**rel, **{m: rel[o] for m, o in canon.items() if o in rel and m != o}}
    out = {f"plain_{k}": v for k, v in metrics(ranked, plain_rel).items()}
    seen, grouped, repeats = set(), [], 0
    for u in ranked:
        c = canon.get(u, u)
        if c in seen:
            grouped.append(DUP)
            repeats += 1
        else:
            seen.add(c)
            grouped.append(c)
    out |= {f"group_{k}": v for k, v in metrics(grouped, rel).items()}
    out["redundant@10"] = repeats
    return out


def run_seed(seed: int, a, queries: list[dict], work: Path) -> dict:
    rng = random.Random(seed)
    base = list(crawldb_records(Path(a.db)))
    mirrors = {url: mirror_url(url) for url, _ in base if rng.random() < a.mirror_rate}
    records = base + [(mirrors[u], perturb(t, rng.choice(RATES), rng)) for u, t in base
                      if u in mirrors]  # fmt: skip
    corpus = work / "corpus.jsonl"
    build(iter(records), corpus, 0, [0.01], seed, 1)
    local.run(corpus, work / "job", ("index", "dedup"))
    src = work / "src.db"
    shutil.copyfile(a.db, src)
    add_mirror_pages(src, mirrors)
    canon = {m: o for o, m in mirrors.items()}
    result = {"seed": seed, "mirrors": len(mirrors), "dbs": {}}
    for name, collapse in (("as_is", False), ("collapsed", True)):
        db = work / f"{name}.db"
        info = load.load(src, corpus, work / "job", db, collapse)
        with Storage(db) as s:
            hidden = {r["url"] for r in s.conn.execute(
                "SELECT url FROM pages WHERE duplicate_of IS NOT NULL")}  # fmt: skip
            rankers = {r: TFRanker(s) if CONFIGS[r] is None else BM25FRanker(s, CONFIGS[r])
                       for r in RANKERS}  # fmt: skip
            per_query = [{r: score([x.url for x in rk.search(q["q"], k=10)], q["rel"], canon)
                          for r, rk in rankers.items()} for q in queries]  # fmt: skip
        result["dbs"][name] = {"per_query": per_query, "collapsed_pages": info.get(
            "collapsed_pages", 0), "hidden_mirrors": len(hidden & set(canon)),
            "hidden_other_pages": len(hidden - set(canon))}  # fmt: skip
    return result


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m corpus_jobs.dup_eval", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("--db", default="data/pydocs.db")
    ap.add_argument("--queries", default="config/eval_queries.yaml")
    ap.add_argument("--mirror-rate", type=float, default=0.3)
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--out", default="")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    status, queries = load_queries(Path(a.queries))
    seeds = []
    for seed in range(1, a.seeds + 1):
        with tempfile.TemporaryDirectory() as tmp:
            seeds.append(run_seed(seed, a, queries, Path(tmp)))
    keys = list(seeds[0]["dbs"]["as_is"]["per_query"][0][RANKERS[0]])
    summary = {}
    for r in RANKERS:
        rows = {}
        for name in ("as_is", "collapsed"):
            # per query, the mean over seeds
            rows[name] = {k: [statistics.mean(s["dbs"][name]["per_query"][i][r][k] for s in seeds)
                              for i in range(len(queries))] for k in keys}  # fmt: skip
        summary[r] = {
            k: {"as_is": round(statistics.mean(rows["as_is"][k]), 3),
                "collapsed": round(statistics.mean(rows["collapsed"][k]), 3),
                "p": paired_test(rows["as_is"][k], rows["collapsed"][k])}
            for k in keys
        }  # fmt: skip
    mirrors = [s["mirrors"] for s in seeds]
    detection = {
        "mirrors_mean": round(statistics.mean(mirrors), 1),
        "mirrors_hidden_share": round(sum(s["dbs"]["collapsed"]["hidden_mirrors"] for s in seeds)
                                      / sum(mirrors), 3),
        "other_pages_hidden_total": sum(s["dbs"]["collapsed"]["hidden_other_pages"] for s in seeds),
    }  # fmt: skip
    print(f"judgments: {status}; {len(queries)} queries; {a.seeds} seeds; mirrors {detection}")
    for r, ms in summary.items():
        print(f"\n{r}")
        for k, v in ms.items():
            print(f"  {k:<16} as is {v['as_is']:.3f}   collapsed {v['collapsed']:.3f}   "
                  f"p = {v['p']:.4f}")  # fmt: skip
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        out = {"provenance": provenance(vars(a)), "judgments": status, "detection": detection,
               "summary": summary, "seeds": [{k: v for k, v in s.items() if k != "dbs"} | {
                   n: {k: v for k, v in d.items() if k != "per_query"}
                   for n, d in s["dbs"].items()} for s in seeds]}  # fmt: skip
        Path(a.out).write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
