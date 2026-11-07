"""Audit evaluation on real re-fetched pages: honest false FAILs vs. forgery detection.

1. collect: fetch the seed packages' source pages (and a few linked docs pages) several times,
   politely (robots.txt, 1 s per host, identifying User-Agent), into data/drift/. Resumable:
   each run stops after --budget seconds and the next run continues.

       python -m crawl_ledger.audit_eval collect --passes 3 --interval 3600

2. evaluate: treat pass 0 as the peer's fetch and passes 1 and 2 as the auditor's two fetches.
   Honest claims are pass 0 as fetched; forged claims are pass 0 with one edit from forgery.py.
   Each audit scheme then judges every claim, on the real drift and again with rebuild noise
   added to every fetch.

       python -m crawl_ledger.audit_eval evaluate --out results/m3/audit_eval.json

Schemes: raw (M3: exact hash of the page, CDN nonces removed), canon (exact hash of the
canonical digest), blocks (canonical digest, block diff, two-fetch rule; canonical_audit.py) and
simhash (accept when the 64-bit SimHash of the canonical text is within 3 bits)."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .audit import FAIL, INCONCLUSIVE, PASS
from .canonical import Digest, digest, hamming, simhash64
from .canonical_audit import decide_digests
from .fetcher import Fetcher, extract_links, source_urls
from .forgery import CONTENT_FORGERIES, FORGERIES, forge, forge_content, rebuild_noise
from .record import content_hash
from .sim import load_seeds

ROOT = Path(__file__).resolve().parents[2]
SCHEMES = ("raw", "canon", "blocks", "simhash")
SIMHASH_BITS = 3
CACHE = "eval-v4"  # bump when page_tally changes


# --- collect ---------------------------------------------------------------------------------


def _key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:20]


def docs_subpages(url: str, content: bytes, limit: int) -> list[str]:
    """A few same-site documentation pages linked from a docs landing page."""
    host = urlparse(url).netloc
    out: list[str] = []
    for a in BeautifulSoup(content, "lxml").find_all("a", href=True):
        link = urljoin(url, a["href"]).split("#", 1)[0]
        p = urlparse(link)
        if p.netloc == host and link != url and p.path.endswith((".html", "/")) and link not in out:
            out.append(link)
        if len(out) >= limit:
            break
    return out


def discover_one(fetcher: Fetcher, pkg: str, subpages: int) -> list[dict]:
    targets: list[dict] = []
    frontier = list(source_urls(pkg).items())
    while frontier:
        source, url = frontier.pop(0)
        targets.append({"pkg": pkg, "source": source, "url": url})
        if source == "pypi":
            res = fetcher.fetch(url)
            if res is not None and res.status == 200:
                frontier.extend(extract_links(source, res.content).items())
        elif source in ("rtd", "docs", "pydocs") and subpages:
            res = fetcher.fetch(url)
            if res is not None and res.status == 200:
                for link in docs_subpages(url, res.content, subpages):
                    targets.append({"pkg": pkg, "source": source, "url": link})
    return targets


def collect(a) -> None:
    out = Path(a.dir)
    out.mkdir(parents=True, exist_ok=True)
    index_path = out / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    fetcher = Fetcher(delay=a.delay)
    started = time.time()
    index.setdefault("targets", [])
    index.setdefault("discovered", [])
    index.setdefault("passes", [])
    seeds = load_seeds(Path(a.seeds), a.limit)
    for pkg in seeds:  # resumable discovery: one seed at a time, saved as it goes
        if pkg in index["discovered"]:
            continue
        if time.time() - started > a.budget:
            index_path.write_text(json.dumps(index, indent=1))
            print(f"discovery: {len(index['discovered'])}/{len(seeds)} seeds; run again")
            return
        index["targets"].extend(discover_one(fetcher, pkg, a.subpages))
        index["discovered"].append(pkg)
        index_path.write_text(json.dumps(index, indent=1))
    unique: dict[str, dict] = {}
    for t in index["targets"]:  # a URL reached from two seeds is fetched once per pass
        unique.setdefault(t["url"], t)
    index["targets"] = list(unique.values())
    n = len(index["targets"])
    while len(index["passes"]) < a.passes or (
        index["passes"] and len(index["passes"][-1]["done"]) < n
    ):
        if not index["passes"] or len(index["passes"][-1]["done"]) >= n:
            if index["passes"] and time.time() - index["passes"][-1]["finished"] < a.interval:
                wait = a.interval - (time.time() - index["passes"][-1]["finished"])
                print(f"next pass in {wait:.0f} s; run collect again then")
                return
            index["passes"].append({"started": time.time(), "finished": 0.0, "done": {}})
        current = index["passes"][-1]
        k = len(index["passes"]) - 1
        for t in index["targets"]:
            if t["url"] in current["done"]:
                continue
            if time.time() - started > a.budget:
                index_path.write_text(json.dumps(index, indent=1))
                print(f"pass {k}: {len(current['done'])}/{n}; run again")
                return
            res = fetcher.fetch(t["url"])
            entry = {"status": None if res is None else res.status, "at": time.time()}
            if res is not None:
                entry["changed_at"] = res.changed_at
                if res.status == 200:
                    path = out / f"p{k}" / f"{_key(t['url'])}.bin"
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(res.content)
            current["done"][t["url"]] = entry
        current["finished"] = time.time()
        index_path.write_text(json.dumps(index, indent=1))
        print(f"pass {k} complete")
    print("collection complete")


# --- evaluate --------------------------------------------------------------------------------


def judge(scheme: str, claim: bytes, first: bytes, changed: bool, d0: Digest, obs: dict) -> str:
    """One scheme's verdict; `obs` caches the auditor-side digests for this page."""
    if scheme == "raw":
        if content_hash(claim) == obs["raw"]:
            return PASS
        return INCONCLUSIVE if changed else FAIL
    if scheme == "canon":
        if d0.root == obs["d1"].root:
            return PASS
        return INCONCLUSIVE if changed else FAIL
    if scheme == "simhash":
        if hamming(simhash64(d0.text()), obs["sim"]) <= SIMHASH_BITS:
            return PASS
        return INCONCLUSIVE if changed else FAIL
    return decide_digests(d0.hashes, d0.version, obs["d1"], obs["d2_root"], changed).verdict


def load_triples(a) -> list[dict]:
    root = Path(a.dir)
    index = json.loads((root / "index.json").read_text())
    passes = index["passes"]
    if len(passes) < 3:
        raise SystemExit(f"need 3 complete passes, have {len(passes)}")
    rows = []
    for t in index["targets"]:
        entries = [p["done"].get(t["url"], {}) for p in passes[:3]]
        if any(e.get("status") != 200 for e in entries):
            continue
        files = [root / f"p{k}" / f"{_key(t['url'])}.bin" for k in range(3)]
        claim_time = entries[0]["at"]
        changed = any((e.get("changed_at") or 0) > claim_time for e in entries[1:])
        rows.append(
            {**t, "content": [f.read_bytes() for f in files], "changed": changed,
             "gap_s": entries[1]["at"] - claim_time}
        )  # fmt: skip
    return rows


def page_tally(i: int, r: dict, drift: str) -> dict:
    """Verdict counts for one page: {case: {scheme: {verdict: n}}}."""
    claim, first, second = r["content"]
    if drift == "real+rebuild":
        claim = rebuild_noise(claim, 3 * i)
        first = rebuild_noise(first, 3 * i + 1)
        second = rebuild_noise(second, 3 * i + 2)
    d1 = digest(first)
    obs = {"raw": content_hash(first), "d1": d1, "d2_root": digest(second).root,
           "sim": simhash64(d1.text())}  # fmt: skip
    out: dict = defaultdict(lambda: defaultdict(int))
    cases = [("honest", claim)]
    cases += [(k, f) for k in FORGERIES if (f := forge(claim, k)) is not None]
    cases += [
        (f"{k}@content", f) for k in CONTENT_FORGERIES if (f := forge_content(claim, k)) is not None
    ]
    for case, claimed in cases:
        d0 = digest(claimed)
        for scheme in SCHEMES:
            out[f"{case}|{scheme}"][judge(scheme, claimed, first, r["changed"], d0, obs)] += 1
    return {k: dict(v) for k, v in out.items()}


def evaluate(a) -> dict:
    """Per-page results are cached under <dir>/eval/, so a run cut short resumes."""
    rows = load_triples(a)
    cache = Path(a.dir) / CACHE
    cache.mkdir(exist_ok=True)
    started = time.time()
    results: dict = {}
    for drift in ("real", "real+rebuild"):
        tally = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        for i, r in enumerate(rows):
            path = cache / f"{drift}-{_key(r['url'])}.json"
            if path.exists():
                counts = json.loads(path.read_text())
            else:
                if time.time() - started > a.budget:
                    done = len(list(cache.glob("*.json")))
                    print(f"{done}/{2 * len(rows)} page evaluations cached; run again")
                    raise SystemExit(0)
                counts = page_tally(i, r, drift)
                path.write_text(json.dumps(counts))
            for key, verdicts in counts.items():
                case, scheme = key.split("|")
                for v, n in verdicts.items():
                    tally[case][scheme][v] += n
        results[drift] = {c: {s: dict(v) for s, v in by.items()} for c, by in tally.items()}
    by_source = defaultdict(int)
    for r in rows:
        by_source[r["source"]] += 1
    summary = {
        "pages": len(rows),
        "by_source": dict(by_source),
        "changed_signal": sum(r["changed"] for r in rows),
        "median_gap_s": sorted(r["gap_s"] for r in rows)[len(rows) // 2] if rows else None,
        "simhash_bits": SIMHASH_BITS,
        "results": results,
    }
    print_table(summary)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(summary, indent=2))
    return summary


def rate(counts: dict, verdict: str) -> str:
    n = sum(counts.values())
    return f"{counts.get(verdict, 0) / n:.0%} ({n})" if n else "-"


def print_table(summary: dict) -> None:
    print(f"{summary['pages']} pages {summary['by_source']}")
    for drift, by_case in summary["results"].items():
        print(f"\n[{drift}]  honest: FAIL rate / INCONCLUSIVE rate; forged: FAIL (detection) rate")
        print("case".ljust(18) + "".join(s.rjust(16) for s in SCHEMES))
        for case, by_scheme in by_case.items():
            cells = []
            for s in SCHEMES:
                c = by_scheme.get(s, {})
                cells.append(
                    f"{rate(c, FAIL)}/{rate(c, INCONCLUSIVE).split(' ')[0]}"
                    if case == "honest"
                    else rate(c, FAIL)
                )
            print(case.ljust(18) + "".join(x.rjust(16) for x in cells))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m crawl_ledger.audit_eval", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--dir", default=str(ROOT / "data" / "drift"))
    c.add_argument("--seeds", default=str(ROOT / "config" / "ledger_seeds.txt"))
    c.add_argument("--limit", type=int, default=25)
    c.add_argument("--subpages", type=int, default=3, help="linked docs pages per docs site")
    c.add_argument("--passes", type=int, default=3)
    c.add_argument("--interval", type=float, default=0.0, help="seconds between passes")
    c.add_argument("--delay", type=float, default=1.0, help="seconds between requests to a host")
    c.add_argument("--budget", type=float, default=150.0, help="seconds per run, then resume")
    e = sub.add_parser("evaluate")
    e.add_argument("--dir", default=str(ROOT / "data" / "drift"))
    e.add_argument("--out", default="")
    e.add_argument("--budget", type=float, default=150.0, help="seconds per run, then resume")
    a = ap.parse_args(argv)
    if a.cmd == "collect":
        collect(a)
    else:
        evaluate(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
