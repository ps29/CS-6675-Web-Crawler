"""Spark pipeline (PySpark RDDs, local mode): read and tokenize the corpus once, then build the
index, the top-k terms and the near-duplicate pairs from the same RDD.

With `--cache` the tokenized RDD is persisted (MEMORY_AND_DISK) and reused by the three jobs,
which is the in-memory reuse the RDD paper (Zaharia et al. 2012) argues for. Without it each job
re-reads and re-tokenizes the input, as separate MapReduce jobs do. `reduceByKey` combines on the
map side, like a MapReduce combiner.

    python -m corpus_jobs.spark_job data/m5/average.jsonl results/m5/out/spark --cores 2 --cache

Shuffle and spill volumes are read from Spark's monitoring REST API at the end of the run and
written, with the job times, to <out>/metrics.json."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from collections import Counter
from operator import add
from pathlib import Path

from .core import MAX_BUCKET, bands, bucket_pairs, pair_line, parse, postings_line, signature
from .core import tokens as tokenize
from .streaming import TOP_K


def _tokenized(line: str):
    doc, text = parse(line)
    return doc, tokenize(text)


def _postings(d):
    return [(t, (d[0], c)) for t, c in Counter(d[1]).items()]


def _sig_bands(d):
    sig = signature(d[1])
    return [] if sig is None else [(b, (d[0], sig)) for b in bands(sig)]


def _pairs(kv):
    members = list(kv[1])
    if len(members) > MAX_BUCKET:
        return []
    return [((a, b), s) for a, b, s in bucket_pairs(members)]


def stage_metrics(ui: str) -> dict:
    with urllib.request.urlopen(f"{ui}/api/v1/applications", timeout=10) as r:
        app = json.load(r)[0]["id"]
    with urllib.request.urlopen(f"{ui}/api/v1/applications/{app}/stages", timeout=10) as r:
        stages = json.load(r)
    keys = ("shuffleWriteBytes", "shuffleReadBytes", "memoryBytesSpilled", "diskBytesSpilled",
            "inputBytes")  # fmt: skip
    return {k: sum(s.get(k, 0) for s in stages) for k in keys} | {"stages": len(stages)}


def run(corpus: Path, out: Path, cores: int, cache: bool, jobs: tuple[str, ...],
        memory: str) -> dict:  # fmt: skip
    from pyspark import SparkConf, SparkContext, StorageLevel

    conf = (SparkConf().setMaster(f"local[{cores}]").setAppName("corpus_jobs")
            .set("spark.driver.memory", memory).set("spark.ui.showConsoleProgress", "false")
            .set("spark.default.parallelism", str(max(2, cores * 2))))  # fmt: skip
    t0 = time.perf_counter()
    sc = SparkContext(conf=conf)
    sc.setLogLevel("ERROR")
    timings = {"startup_s": time.perf_counter() - t0}
    out.mkdir(parents=True, exist_ok=True)
    docs = sc.textFile(str(corpus), minPartitions=max(2, cores * 2)).map(_tokenized)
    if cache:
        docs = docs.persist(StorageLevel.MEMORY_AND_DISK)
    if "index" in jobs:
        t = time.perf_counter()
        index = docs.flatMap(_postings).groupByKey()
        index.map(lambda kv: postings_line(kv[0], list(kv[1]))).saveAsTextFile(str(out / "index"))
        timings["index_s"] = time.perf_counter() - t
    if "topk" in jobs:
        t = time.perf_counter()
        counts = docs.flatMap(lambda d: Counter(d[1]).items()).reduceByKey(add)
        best = counts.takeOrdered(TOP_K, key=lambda tc: (-tc[1], tc[0]))
        (out / "topk.tsv").write_text("".join(f"{t}\t{c}\n" for t, c in best), encoding="utf-8")
        timings["topk_s"] = time.perf_counter() - t
    if "dedup" in jobs:
        t = time.perf_counter()
        pairs = docs.flatMap(_sig_bands).groupByKey().flatMap(_pairs).reduceByKey(max)
        pairs.map(lambda kv: pair_line(*kv[0], kv[1])).saveAsTextFile(str(out / "pairs"))
        timings["dedup_s"] = time.perf_counter() - t
    try:
        metrics = stage_metrics(sc.uiWebUrl)
    except OSError as e:  # the UI may be disabled; timings are still valid
        metrics = {"error": str(e)}
    sc.stop()
    result = {"timings": timings, "spark": metrics}
    (out / "metrics.json").write_text(json.dumps(result, indent=1))
    return result


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m corpus_jobs.spark_job", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("corpus")
    ap.add_argument("out")
    ap.add_argument("--cores", type=int, default=2)
    ap.add_argument("--cache", action="store_true")
    ap.add_argument("--jobs", nargs="+", default=["index", "topk", "dedup"])
    ap.add_argument("--memory", default="3g", help="driver (= executor) memory in local mode")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    run(Path(a.corpus), Path(a.out), a.cores, a.cache, tuple(a.jobs), a.memory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
