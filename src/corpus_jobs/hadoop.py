"""Hadoop MapReduce pipelines over Hadoop Streaming (local job runner, local file system).

Each logical step is its own MapReduce job, and jobs hand data to each other only through files,
as in the original model (Dean and Ghemawat 2004):

    index   corpus -> inverted index                                  1 job
    topk    corpus -> term counts -> top-k terms                       2 jobs (top-k: 1 reducer)
    dedup   corpus -> LSH buckets -> candidate pairs -> distinct pairs  2 jobs

`naive` emits a record per token occurrence and has no combiner; its top-k job sends every term
to the single reducer. `refined` aggregates before the shuffle (in-mapper combining per
document and per task, a combiner for term counts, a local top-k in every map task). The dedup
jobs are the same in both. Every job re-reads and re-tokenizes the corpus.

Requires HADOOP_HOME (Hadoop 3.x) and Java; Python mappers run with this package on PYTHONPATH."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
SHUFFLE_BUFFER = 0.35  # share of reducer heap for in-memory map output (Hadoop default 0.70)
COUNTERS = {
    "map_output_records": "Map output records",
    "map_output_bytes": "Map output bytes",
    "map_output_materialized_bytes": "Map output materialized bytes",
    "reduce_shuffle_bytes": "Reduce shuffle bytes",
    "spilled_records": "Spilled Records",
    "combine_input_records": "Combine input records",
    "skipped_buckets": "skipped_buckets",
}


@dataclass(frozen=True)
class Job:
    name: str
    mapper: str
    reducer: str
    combiner: str | None = None
    reducers: int | None = None  # None: one per core
    input_from: str | None = None  # a previous job's output instead of the corpus


def py(role: str) -> str:
    return f"{sys.executable} -m corpus_jobs.streaming {role}"


PIPELINES: dict[str, dict[str, list[Job]]] = {
    "naive": {
        "index": [Job("index", py("index_map_naive"), py("index_reduce_naive"))],
        "topk": [Job("wc", py("wc_map_naive"), py("wc_reduce")),
                 Job("topk", py("topk_map_naive"), py("topk_reduce"), reducers=1,
                     input_from="wc")],
    },
    "refined": {
        "index": [Job("index", py("index_map"), py("index_reduce"))],
        "topk": [Job("wc", py("wc_map"), py("wc_reduce"), combiner=py("wc_reduce")),
                 Job("topk", py("topk_map"), py("topk_reduce"), reducers=1,
                     input_from="wc")],
    },
}  # fmt: skip
DEDUP = [Job("bands", py("dedup_map"), py("dedup_reduce")),
         Job("pairs", "cat", py("pairs_reduce"), input_from="bands")]  # fmt: skip
for _p in PIPELINES.values():
    _p["dedup"] = DEDUP


def mapred() -> str:
    home = os.environ.get("HADOOP_HOME")
    if not home:
        raise SystemExit("set HADOOP_HOME to a Hadoop 3.x installation")
    return str(Path(home) / "bin" / "mapred")


def parse_counters(log: str) -> dict[str, int]:
    out = {}
    for key, label in COUNTERS.items():
        out[key] = sum(int(m) for m in re.findall(rf"\b{re.escape(label)}=(\d+)", log))
    return out


def run_job(job: Job, corpus: Path, work: Path, cores: int) -> dict:
    src = work / job.input_from if job.input_from else corpus
    dst = work / job.name
    shutil.rmtree(dst, ignore_errors=True)
    reducers = job.reducers or cores
    cmd = [mapred(), "streaming",
           "-D", "mapreduce.framework.name=local", "-D", "fs.defaultFS=file:///",
           "-D", f"mapreduce.job.reduces={reducers}",
           "-D", f"mapreduce.local.map.tasks.maximum={cores}",
           "-D", f"mapreduce.local.reduce.tasks.maximum={cores}",
           "-D", "mapreduce.job.name=corpus_jobs." + job.name,
           # The local runner runs every reducer in one JVM, and each may hold up to 70% of the
           # heap of map output in memory; two at once ran out of heap on the 716 MB corpus.
           "-D", f"mapreduce.reduce.shuffle.input.buffer.percent={SHUFFLE_BUFFER}",
           "-cmdenv", f"PYTHONPATH={SRC}",
           "-input", str(src), "-output", str(dst),
           "-mapper", job.mapper, "-reducer", job.reducer]  # fmt: skip
    if job.combiner:
        cmd += ["-combiner", job.combiner]
    t = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.perf_counter() - t
    if proc.returncode != 0 or "completed successfully" not in proc.stderr:
        log = work / f"{job.name}.stderr.log"  # the tail alone rarely shows the cause
        log.write_text(proc.stderr, encoding="utf-8")
        tail = "\n".join(proc.stderr.splitlines()[-30:])
        raise RuntimeError(f"Hadoop job {job.name} failed (full log: {log}):\n{tail}")
    return {"job": job.name, "seconds": elapsed, **parse_counters(proc.stderr)}


def run(corpus: Path, out: Path, cores: int, variant: str, jobs: tuple[str, ...]) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for step in jobs:
        for job in PIPELINES[variant][step]:
            results.append(run_job(job, corpus, out, cores))
    totals = {k: sum(r[k] for r in results) for k in COUNTERS}
    return {"jobs": results, "counters": totals}
