"""Run one engine on one corpus: `python -m corpus_jobs.run ENGINE CORPUS OUT [--cores N]`.

Engines: `local` (single node), `hadoop-naive`, `hadoop` (refined), `hadoop-lsh` (refined, with
the compact near-duplicate shuffle), `spark` (cached RDD) and
`spark-nocache`. Writes <OUT>/metrics.json with wall time and engine counters. `bench.py` runs
this in a subprocess so that it can sample the memory of the whole process tree."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ENGINES = ("local", "hadoop-naive", "hadoop", "hadoop-lsh", "spark", "spark-nocache")
JOBS = ("index", "topk", "dedup")


def run(engine: str, corpus: Path, out: Path, cores: int, jobs: tuple[str, ...],
        hdfs: str | None = None) -> dict:  # fmt: skip
    t = time.perf_counter()
    if engine == "local":
        from . import local

        detail = local.run(corpus, out, jobs)
    elif engine.startswith("hadoop"):
        from . import hadoop

        variant = {"hadoop-naive": "naive", "hadoop": "refined", "hadoop-lsh": "lsh"}[engine]
        detail = hadoop.run(corpus, out, cores, variant, jobs, hdfs)
    else:
        from . import spark_job

        detail = spark_job.run(corpus, out, cores, engine == "spark", jobs, "3g")
    result = {"engine": engine, "cores": cores, "jobs": list(jobs),
              "wall_s": time.perf_counter() - t, "detail": detail}  # fmt: skip
    (out / "metrics.json").write_text(json.dumps(result, indent=1))
    return result


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m corpus_jobs.run", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("engine", choices=ENGINES)
    ap.add_argument("corpus")
    ap.add_argument("out")
    ap.add_argument("--cores", type=int, default=2)
    ap.add_argument("--jobs", nargs="+", default=list(JOBS), choices=JOBS)
    ap.add_argument(
        "--hdfs", help="Hadoop engines: run on the YARN cluster, in this HDFS directory"
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    r = run(a.engine, Path(a.corpus), Path(a.out), a.cores, tuple(a.jobs), a.hdfs)
    print(f"{a.engine}: {r['wall_s']:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
