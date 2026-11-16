"""The crawl-database corpus source and the loader that hands a job's index to the M1 rankers."""

import json

import pytest

pytest.importorskip("numpy")

from corpus_jobs import corpus, load, local  # noqa: E402
from focused_crawler.ranker import BM25FRanker, TFRanker  # noqa: E402
from focused_crawler.storage import Storage  # noqa: E402

LONG = "x" * 45  # longer than the job tokenizer's 40-character limit
BODY = " ".join(f"w{i}" for i in range(60))
PAGES = {
    "https://a.example/json": f"json dump encod {BODY}",
    "https://a.example/json-copy": f"json dump encod {BODY}",
    "https://a.example/math": f"math sqrt json {LONG} float number root squar",
}


def make_db(path):
    with Storage(path) as s:
        for url, text in PAGES.items():
            pid = s.upsert_page(url, "a.example", 0, 200, title=url.rsplit("/", 1)[1])
            positions = {}
            for i, term in enumerate(text.split()):
                positions.setdefault(term, []).append(i)
            s.replace_postings(pid, positions)


def run_pipeline(tmp_path, collapse=False, verify=False):
    db, out_db = tmp_path / "src.db", tmp_path / "out.db"
    make_db(db)
    jsonl = tmp_path / "corpus.jsonl"
    corpus.build(corpus.crawldb_records(db), jsonl, 0, [0.01], 1, 1)
    local.run(jsonl, tmp_path / "job", ("index", "dedup"))
    return db, out_db, load.load(db, jsonl, tmp_path / "job", out_db, collapse, verify)


def test_crawldb_records_rebuild_the_token_stream(tmp_path):
    make_db(tmp_path / "src.db")
    records = dict(corpus.crawldb_records(tmp_path / "src.db"))
    assert records["https://a.example/json"] == PAGES["https://a.example/json"]
    assert LONG not in records["https://a.example/math"]  # over-long terms are left out


def test_loaded_index_matches_the_m1_index(tmp_path):
    _db, _out, result = run_pipeline(tmp_path, verify=True)
    c = result["check"]
    assert result["documents"] == 3 and result["skipped_documents"] == 0
    assert c["loaded_postings"] == c["identical"] == c["source_postings"] - 1  # minus LONG


def test_rankers_agree_on_source_and_loaded_index(tmp_path):
    db, out_db, _ = run_pipeline(tmp_path)
    for ranker in (TFRanker, BM25FRanker):
        with Storage(db) as a, Storage(out_db) as b:
            want = [(r.url, round(r.score, 6)) for r in ranker(a).search("json math")]
            got = [(r.url, round(r.score, 6)) for r in ranker(b).search("json math")]
        assert want and [u for u, _ in got] == [u for u, _ in want]


def test_collapse_hides_the_near_duplicate_page(tmp_path):
    _db, out_db, result = run_pipeline(tmp_path, collapse=True)
    assert result["collapsed_pages"] == 1 and result["indexed_pages"] == 2
    with Storage(out_db) as s:
        urls = [r.url for r in TFRanker(s).search("json dump")]
    assert "https://a.example/json" in urls and "https://a.example/json-copy" not in urls


def test_source_database_is_left_untouched(tmp_path):
    db, _out, _ = run_pipeline(tmp_path, collapse=True)
    with Storage(db) as s:
        assert s.summary()["duplicates"] == 0
    with pytest.raises(SystemExit):
        load.load(db, tmp_path / "corpus.jsonl", tmp_path / "job", db, False)


def test_ledger_snapshot_becomes_a_searchable_index_with_record_ids(tmp_path):
    from crawl_ledger.snapshot import build_parser, run

    snap = tmp_path / "ledger"
    args = build_parser().parse_args(
        ["--offline", "--quiet", "--dishonest", "0.5", "--forge-rate", "0.5", "--audit-rate", "0.5"]
    )
    made = run(args, snap)
    jsonl = tmp_path / "ledger.jsonl"
    records = corpus.ledger_records(snap, "pass", tmp_path / "ledger.jsonl.records.json")
    meta = corpus.build(records, jsonl, 0, [0.01], 1, 1)
    assert meta["docs"] == made["verdicts"]["PASS"]  # only audited-PASS pages are indexed
    local.run(jsonl, tmp_path / "job", ("index", "dedup"))
    result = load.load(None, jsonl, tmp_path / "job", tmp_path / "search.db", False)
    assert result["indexed_pages"] == meta["docs"]
    with Storage(tmp_path / "search.db") as s:
        hits = TFRanker(s).search("content", k=100)  # the offline pages all say "content of <url>"
    sidecar = json.loads((tmp_path / "ledger.jsonl.records.json").read_text())
    assert sidecar["verification"]["forged_accepted"] == 0
    assert {h.url for h in hits} == set(sidecar["records"])
    for h in hits:
        assert h.fields["audit"] == "PASS"
        assert sidecar["records"][h.url]["id"].startswith(h.fields["record"])
