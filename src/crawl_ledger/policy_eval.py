"""How much does each indexing policy let through? Offline sweep over audit rates and seeds.

    python -m crawl_ledger.policy_eval --seeds 20 --out results/project/ledger_policy.json

For each audit rate and seed, an offline crawl with simulated peers is written as a snapshot
(`crawl_ledger.snapshot`) and read back under both policies. The table reports, per policy, the
share of honest records that reach the index (coverage) and the number of forged records that do.

Offline content never changes, so an honest record is never INCONCLUSIVE here. On live pages
some honest records are, and the `pass` policy leaves those out as well.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
from pathlib import Path

from runinfo import provenance

from .snapshot import POLICIES, accepted, run
from .snapshot import build_parser as snapshot_parser


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m crawl_ledger.policy_eval", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("--seeds", type=int, default=20, help="number of seeds (1..N)")
    ap.add_argument("--audit-rates", type=float, nargs="+", default=[0.2, 0.5, 0.8, 1.0])
    ap.add_argument("--peers", type=int, default=20)
    ap.add_argument("--dishonest", type=float, default=0.2)
    ap.add_argument("--forge-rate", type=float, default=0.5)
    ap.add_argument("--limit", type=int, default=100, help="packages per crawl")
    ap.add_argument("--out", default="")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    rows = []
    for rate in a.audit_rates:
        cells = {p: {"coverage": [], "forged_in": [], "forged": []} for p in POLICIES}
        for seed in range(1, a.seeds + 1):
            args = snapshot_parser().parse_args([
                "--offline", "--quiet", "--peers", str(a.peers), "--dishonest", str(a.dishonest),
                "--forge-rate", str(a.forge_rate), "--audit-rate", str(rate),
                "--limit", str(a.limit), "--seed", str(seed),
            ])  # fmt: skip
            with tempfile.TemporaryDirectory() as tmp:
                made = run(args, Path(tmp))
                honest = made["records"] - made["forged"]
                for policy in POLICIES:
                    stats = accepted(Path(tmp), policy)[1]
                    cell = cells[policy]
                    cell["coverage"].append((stats["accepted"] - stats["forged_accepted"]) / honest)
                    cell["forged_in"].append(stats["forged_accepted"])
                    cell["forged"].append(made["forged"])
        for policy in POLICIES:
            cell = cells[policy]
            rows.append({
                "audit_rate": rate, "policy": policy, "runs": a.seeds,
                "honest_coverage_mean": round(statistics.mean(cell["coverage"]), 3),
                "forged_published_mean": round(statistics.mean(cell["forged"]), 2),
                "forged_indexed_mean": round(statistics.mean(cell["forged_in"]), 2),
                "forged_indexed_max": max(cell["forged_in"]),
                "runs_with_a_forged_page_indexed": sum(x > 0 for x in cell["forged_in"]),
            })  # fmt: skip
    for r in rows:
        print(f"audit {r['audit_rate']:.1f}  {r['policy']:<10}  coverage "
              f"{r['honest_coverage_mean']:.3f}  forged indexed {r['forged_indexed_mean']:.2f} of "
              f"{r['forged_published_mean']:.2f} (max {r['forged_indexed_max']}, in "
              f"{r['runs_with_a_forged_page_indexed']}/{r['runs']} runs)")  # fmt: skip
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        out = {"provenance": provenance(vars(a)), "config": vars(a), "rows": rows}
        Path(a.out).write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
