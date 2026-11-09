"""M5 corpus jobs: shared record logic, Streaming roles, and engine agreement (offline)."""

import gzip
import io
import json
import os
import random
import shutil
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from corpus_jobs import core, local, streaming  # noqa: E402
from corpus_jobs.bench import canonical  # noqa: E402
from corpus_jobs.corpus import build, perturb, wet_records  # noqa: E402

TEXTS = [
    "Python json module encodes objects as JSON text and decodes JSON text into objects " * 3,
    "The asyncio event loop runs coroutines and callbacks and handles network IO " * 3,
    "Install the package with pip and import it in your project to get started " * 3,
]


@pytest.fixture
def corpus(tmp_path):
    rng = random.Random(0)
    records = [(f"https://x.example/{i}", t) for i, t in enumerate(TEXTS)]
    records.append(("https://x.example/copy", perturb(TEXTS[0], 0.0, rng)))  # exact copy
    out = tmp_path / "c.jsonl"
    build(iter(records), out, inject=0, rates=[0.01], seed=0, min_tokens=5)
    return out


def run_role(role, lines):
    out = io.StringIO()
    streaming.ROLES[role](io.StringIO("".join(lines)), out)
    return out.getvalue().splitlines(keepends=True)


def shuffle(lines):
    """What Hadoop does between map and reduce: sort by the key before the first tab."""
    return sorted(lines, key=lambda x: x.split("\t", 1)[0])


def test_tokens_and_postings_line():
    assert core.tokens("Hello, World! a x_y 3.14") == ["hello", "world", "14"]
    assert core.postings_line("t", [("d2", 1), ("d1", 3)]) == "t\t2\td1:3,d2:1"


def test_minhash_estimates_jaccard():
    a = core.tokens(TEXTS[0] * 4)
    b = core.tokens(perturb(TEXTS[0] * 4, 0.02, random.Random(1)))
    est = core.estimate(core.signature(a), core.signature(b))
    assert abs(est - core.exact_jaccard(a, b)) < 0.15
    assert core.signature([]) is None
    assert len(core.bands(core.signature(a))) == core.BANDS


def test_signature_is_stable_across_processes():
    # CRC-32 shingles and a fixed-seed hash family, not Python's salted hash()
    sig = core.signature(core.tokens("one two three four five six seven"))
    assert core.sig_from_str(core.sig_to_str(sig)).tolist() == sig.tolist()


@pytest.mark.parametrize("naive", [True, False])
def test_streaming_index_matches_local(corpus, tmp_path, naive):
    local.run(corpus, tmp_path / "local", ("index",))
    m, r = ("index_map_naive", "index_reduce_naive") if naive else ("index_map", "index_reduce")
    lines = run_role(r, shuffle(run_role(m, corpus.read_text().splitlines(True))))
    assert sorted(x.rstrip("\n") for x in lines) == canonical(tmp_path / "local", "index")


@pytest.mark.parametrize("naive", [True, False])
def test_streaming_topk_matches_local(corpus, tmp_path, naive):
    local.run(corpus, tmp_path / "local", ("topk",))
    src = corpus.read_text().splitlines(True)
    if naive:
        counts = run_role("wc_reduce", shuffle(run_role("wc_map_naive", src)))
        top = run_role("topk_reduce", shuffle(run_role("topk_map_naive", counts)))
    else:
        mapped = run_role("wc_reduce", shuffle(run_role("wc_map", src)))  # combiner
        counts = run_role("wc_reduce", shuffle(mapped))
        top = run_role("topk_reduce", shuffle(run_role("topk_map", counts)))
    assert [x.rstrip("\n") for x in top] == canonical(tmp_path / "local", "topk")


def test_streaming_dedup_finds_the_copy(corpus, tmp_path):
    local.run(corpus, tmp_path / "local", ("dedup",))
    mapped = run_role("dedup_map", corpus.read_text().splitlines(True))
    buckets = run_role("dedup_reduce", shuffle(mapped))
    pairs = [x.rstrip("\n") for x in run_role("pairs_reduce", shuffle(buckets))]
    assert pairs == canonical(tmp_path / "local", "dedup")
    assert pairs == ["d0000000\td0000003\t1.0000"]


def test_wet_reader(tmp_path):
    body = b"hello wet text"
    head = b"WARC/1.0\r\nWARC-Type: conversion\r\nWARC-Target-URI: https://a.example/\r\n"
    rec = head + b"Content-Length: %d\r\n\r\n" % len(body) + body + b"\r\n\r\n"
    info = b"WARC/1.0\r\nWARC-Type: warcinfo\r\nContent-Length: 2\r\n\r\nhi\r\n\r\n"
    path = tmp_path / "x.wet.gz"
    with gzip.open(path, "wb") as f:
        f.write(info + rec)
    assert list(wet_records(path)) == [("https://a.example/", "hello wet text")]


def test_injected_duplicates_have_ground_truth(tmp_path):
    out = tmp_path / "c.jsonl"
    records = [(f"u{i}", " ".join(f"w{i}x{j}" for j in range(60))) for i in range(10)]
    meta = build(iter(records), out, inject=4, rates=[0.01], seed=3, min_tokens=5)
    assert meta["docs"] == 14 and len(meta["truth"]) == 4
    assert all(t["b"].startswith(t["a"] + "~dup") for t in meta["truth"])
    assert json.loads(Path(f"{out}.truth.json").read_text())["injected"] == 4


@pytest.mark.skipif(not os.environ.get("HADOOP_HOME") or not shutil.which("java"),
                    reason="needs HADOOP_HOME and Java")  # fmt: skip
def test_hadoop_matches_local(corpus, tmp_path):
    from corpus_jobs import hadoop

    local.run(corpus, tmp_path / "local", ("index", "topk", "dedup"))
    hadoop.run(corpus, tmp_path / "hadoop", 1, "refined", ("index", "topk", "dedup"))
    for job in ("index", "topk", "dedup"):
        assert canonical(tmp_path / "hadoop", job) == canonical(tmp_path / "local", job), job


def test_estimate_is_linear_in_input_size():
    from corpus_jobs.estimate import estimate

    def cell(t, m, b):
        return {"bytes": b, "engines": {"local@2": {"wall_s_median": t, "peak_rss_mb_max": m}}}

    bench = {"scenarios": {"easy": cell(1.0, 100, 10), "average": cell(11.0, 200, 100)}}
    r = estimate(bench, {"docs": 4, "bytes": 400}, "easy", "average", 2)["engines"]["local"]
    assert r["target_s_est"] == 41 and r["target_peak_mb_est"] == 500
