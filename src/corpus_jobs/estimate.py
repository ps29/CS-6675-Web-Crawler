"""Estimate a scenario that was not run from two that were (a linear-in-input model).

For each engine, the time on the target corpus is estimated as

    T_target = T_small + (T_large - T_small) * (bytes_target / bytes_large)

where T_small (here the 85-page corpus) stands for the fixed start-up cost and the rest scales
with input size. Single-node peak memory is estimated the same way. These are estimates, not
measurements, and are labelled as such wherever they are reported.

    python -m corpus_jobs.estimate --out results/m5/hard_estimate.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .run import ENGINES

ROOT = Path(__file__).resolve().parents[2]


def estimate(bench: dict, target: dict, small: str, large: str, cores: int) -> dict:
    sc = bench["scenarios"]
    ratio = target["bytes"] / sc[large]["bytes"]
    out = {"method": "T_target = T_small + (T_large - T_small) * bytes_target / bytes_large; "
           "estimates, not measurements", "small": small, "large": large, "cores": cores,
           "bytes_ratio": round(ratio, 3), "target_docs": target["docs"],
           "target_bytes": target["bytes"], "engines": {}}  # fmt: skip
    for e in ENGINES:
        key = f"{e}@{cores}"
        if key not in sc[small]["engines"] or key not in sc[large]["engines"]:
            continue
        ts, tl = (sc[s]["engines"][key]["wall_s_median"] for s in (small, large))
        row = {"small_s": ts, "large_s": tl, "target_s_est": round(ts + (tl - ts) * ratio)}
        if e == "local":
            ms, ml = (sc[s]["engines"][key]["peak_rss_mb_max"] for s in (small, large))
            row["target_peak_mb_est"] = round(ms + (ml - ms) * ratio)
        out["engines"][e] = row
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m corpus_jobs.estimate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("--bench", default=str(ROOT / "results" / "m5" / "bench.json"))
    ap.add_argument(
        "--target",
        default=str(ROOT / "data" / "m5" / "hard.jsonl.truth.json"),
        help="the target corpus's .truth.json (docs and bytes)",
    )
    ap.add_argument("--small", default="easy")
    ap.add_argument("--large", default="average")
    ap.add_argument("--cores", type=int, default=2)
    ap.add_argument("--out", default="")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    target = json.loads(Path(a.target).read_text())
    result = estimate(json.loads(Path(a.bench).read_text()), target, a.small, a.large, a.cores)
    print(json.dumps(result["engines"], indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
