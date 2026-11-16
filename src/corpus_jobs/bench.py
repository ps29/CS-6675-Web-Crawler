"""M5 benchmark: every engine on every scenario, with outputs checked against the single node.

    python -m corpus_jobs.bench --scenarios easy average --engines local hadoop spark \
        --repeats 3 --out results/m5/bench.json

Scenarios are corpus files built by `corpus_jobs.corpus` (data/m5/<name>.jsonl). Each run is a
subprocess (`corpus_jobs.run`); the peak resident memory of its whole process tree (JVMs and
Python workers included) is sampled every 0.2 s. After each run the outputs are sorted into a
canonical form and hashed; every engine must match the single-node baseline exactly.

Near-duplicate quality: the injected copies whose exact shingle Jaccard is at least THRESHOLD
are the positives. Recall is the share of positives found; precision is the share of reported
pairs whose exact Jaccard, recomputed from the corpus, is at least THRESHOLD (so it measures the
MinHash/LSH approximation, including natural duplicates already in the crawl)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

from runinfo import provenance

from .run import ENGINES, JOBS

ROOT = Path(__file__).resolve().parents[2]


def peak_rss(proc: subprocess.Popen, stop: threading.Event, out: list) -> None:
    import psutil

    peak = 0
    p = psutil.Process(proc.pid)
    while not stop.is_set():
        try:
            rss = p.memory_info().rss + sum(c.memory_info().rss for c in p.children(True))
            peak = max(peak, rss)
        except psutil.Error:
            pass
        time.sleep(0.2)
    out.append(peak)


def canonical(out: Path, job: str) -> list[str]:
    name = "pairs" if job == "dedup" else job
    if (out / f"{name}.tsv").exists():
        lines = (out / f"{name}.tsv").read_text(encoding="utf-8").splitlines()
    else:
        parts = sorted((out / name).glob("part-*"))
        lines = [x for p in parts for x in p.read_text(encoding="utf-8").splitlines()]
    return lines if job == "topk" else sorted(lines)


def digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def run_once(engine: str, corpus: Path, out: Path, cores: int) -> dict:
    cmd = [sys.executable, "-m", "corpus_jobs.run", engine, str(corpus), str(out),
           "--cores", str(cores)]  # fmt: skip
    shutil.rmtree(out, ignore_errors=True)  # Spark refuses to overwrite earlier output
    env = {**os.environ, "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}"}
    stop, peak = threading.Event(), []
    t = time.perf_counter()
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)  # fmt: skip
    sampler = threading.Thread(target=peak_rss, args=(proc, stop, peak))
    sampler.start()
    _, err = proc.communicate()
    stop.set()
    sampler.join()
    wall = time.perf_counter() - t
    if proc.returncode != 0:
        raise RuntimeError(f"{engine} failed:\n{err[-3000:]}")
    metrics = json.loads((out / "metrics.json").read_text())
    return {"engine": engine, "cores": cores, "wall_s": round(wall, 2),
            "inner_s": round(metrics["wall_s"], 2), "peak_rss_mb": round(peak[0] / 2**20),
            "detail": metrics["detail"],
            "digests": {j: digest(canonical(out, j)) for j in JOBS}}  # fmt: skip


def dedup_quality(corpus: Path, out: Path) -> dict:
    from .core import THRESHOLD, exact_jaccard, parse, tokens

    meta = json.loads(Path(f"{corpus}.truth.json").read_text())
    found = {}
    for line in canonical(out, "dedup"):
        a, b, s = line.split("\t")
        found[(a, b)] = float(s)
    need = {d for pair in found for d in pair}
    need |= {t[k] for t in meta["truth"] for k in ("a", "b")}
    toks = {}
    with corpus.open(encoding="utf-8") as f:
        for line in f:
            doc, text = parse(line)
            if doc in need:
                toks[doc] = tokens(text)
    exact = {p: exact_jaccard(toks[p[0]], toks[p[1]]) for p in found}
    positives = [(t["a"], t["b"]) for t in meta["truth"] if t["jaccard"] >= THRESHOLD]
    by_rate: dict[str, list[int]] = {}
    for t in meta["truth"]:
        by_rate.setdefault(str(t["edit_rate"]), []).append(int((t["a"], t["b"]) in found))
    true_found = sum(v >= THRESHOLD for v in exact.values())
    injected = {(t["a"], t["b"]) for t in meta["truth"]}
    return {
        "reported_pairs": len(found),
        "reported_natural_pairs": sum(p not in injected for p in found),
        "precision": round(true_found / len(found), 4) if found else None,
        "injected_positives": len(positives),
        "recall_injected": round(sum(p in found for p in positives) / len(positives), 4)
        if positives
        else None,  # fmt: skip
        "found_share_by_edit_rate": {r: round(sum(v) / len(v), 3) for r, v in by_rate.items()},
        "mean_abs_estimate_error": round(
            statistics.mean(abs(found[p] - exact[p]) for p in found), 4
        )
        if found
        else None,  # fmt: skip
    }


def summarize(runs: list[dict]) -> dict:
    walls = [r["wall_s"] for r in runs]
    s = {"runs": len(runs), "wall_s_median": round(statistics.median(walls), 2),
         "wall_s_min": min(walls), "wall_s_max": max(walls),
         "peak_rss_mb_max": max(r["peak_rss_mb"] for r in runs)}  # fmt: skip
    d = runs[0]["detail"]
    if "counters" in d:
        s["hadoop"] = d["counters"]
        s["hadoop_job_s"] = {j["job"]: round(j["seconds"], 1) for j in d["jobs"]}
        s["hadoop_jobs"] = {j["job"]: {k: j[k] for k in ("map_output_records",
                                                         "reduce_shuffle_bytes")}
                            for j in d["jobs"]}  # fmt: skip
    if "spark" in d:
        s["spark"] = d["spark"]
        s["spark_job_s"] = {k: round(v, 1) for k, v in d["timings"].items()}
    return s


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m corpus_jobs.bench", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("--scenarios", nargs="+", default=["easy", "average", "hard"])
    ap.add_argument("--engines", nargs="+", default=list(ENGINES), choices=ENGINES)
    ap.add_argument("--cores", type=int, nargs="+", default=[2])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--data", default=str(ROOT / "data" / "m5"))
    ap.add_argument("--work", default=str(ROOT / "data" / "m5" / "out"))
    ap.add_argument("--out", default="")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    from .core import THRESHOLD

    result = {"provenance": provenance(vars(a)), "threshold": THRESHOLD, "scenarios": {}}
    if a.out:
        out_path = Path(a.out)
        if out_path.exists():  # resume: keep finished cells
            result["scenarios"] = json.loads(out_path.read_text()).get("scenarios", {})
    for sc in a.scenarios:
        corpus = Path(a.data) / f"{sc}.jsonl"
        meta = json.loads(Path(f"{corpus}.truth.json").read_text())
        cell = result["scenarios"].setdefault(sc, {"docs": meta["docs"], "bytes": meta["bytes"],
                                                    "engines": {}})  # fmt: skip
        for cores in a.cores:
            for engine in a.engines:
                key = f"{engine}@{cores}"
                if key in cell["engines"]:
                    continue
                runs = []
                for i in range(a.repeats):
                    out = Path(a.work) / sc / key
                    r = run_once(engine, corpus, out, cores)
                    runs.append(r)
                    print(f"{sc} {key} run {i + 1}: {r['wall_s']} s, {r['peak_rss_mb']} MB",
                          flush=True)  # fmt: skip
                base = cell["engines"].get("local@2") or cell["engines"].get(f"local@{cores}")
                entry = summarize(runs) | {"digests": runs[0]["digests"]}
                if engine == "local":
                    entry["dedup"] = dedup_quality(corpus, Path(a.work) / sc / key)
                elif base:
                    entry["matches_local"] = entry["digests"] == base["digests"]
                cell["engines"][key] = entry
                if a.out:
                    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                    Path(a.out).write_text(json.dumps(result, indent=1))
    print(json.dumps({sc: {k: v["wall_s_median"] for k, v in c["engines"].items()}
                      for sc, c in result["scenarios"].items()}, indent=1))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
