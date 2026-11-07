"""BM25F ranking, query intent, named-package boost and the evaluation metrics."""

import json

import pytest

from focused_crawler.evaluate import metrics, paired_test
from focused_crawler.indexer import tokenize
from focused_crawler.ranker import BM25FParams, BM25FRanker, query_intent
from focused_crawler.storage import Storage

PAGES = [  # url, host, title, label, package, module, symbols, body
    ("https://a.example/api/json.html", "a.example", "json - JSON encoder", "REFERENCE",
     "python", "json", ["json.dumps", "json.loads"], "encode objects as JSON text"),
    ("https://a.example/howto/json.html", "a.example", "How to read JSON files", "HOWTO",
     "python", None, [], "read json files with the json module json json"),
    ("https://a.example/whatsnew/3.15.html", "a.example", "What's new in 3.15", "CHANGELOG",
     "python", None, [], "json module changes"),
    ("https://b.example/api/", "b.example", "API reference", "REFERENCE",
     "flask", None, ["flask.Flask"], "the flask application object json helpers"),
    ("https://b.example/", "b.example", "Welcome", "OTHER", None, None, [], "flask web framework"),
]  # fmt: skip


@pytest.fixture
def storage():
    s = Storage(":memory:")
    for i, (url, host, title, label, pkg, module, symbols, body) in enumerate(PAGES, 1):
        tokens = tokenize(f"{title} {body}")
        s.conn.execute(
            "INSERT INTO pages (id, url, host, depth, status, title, label, doc_length, fetched_at)"
            " VALUES (?, ?, ?, 0, 200, ?, ?, ?, 0)",
            (i, url, host, title, label, len(tokens)),
        )
        for term in set(tokens):
            s.conn.execute("INSERT INTO postings VALUES (?, ?, ?, '[]')",
                           (term, i, tokens.count(term)))  # fmt: skip
        if pkg:
            s.conn.execute(
                "INSERT INTO doc_pages (page_id, package, kind, title, module, symbols)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (i, pkg, label.lower(), title, module, json.dumps(symbols)),
            )
    yield s
    s.close()


@pytest.mark.parametrize("query,labels", [
    ("how to read json", {"HOWTO", "TUTORIAL"}),
    ("json.dumps", {"REFERENCE"}),
    ("what's new in 3.15", {"CHANGELOG"}),
    ("json", set()),
])  # fmt: skip
def test_query_intent(query, labels):
    assert query_intent(query) == labels


def test_symbol_in_meta_field_ranks_reference_first(storage):
    top = BM25FRanker(storage).search("json.dumps")[0]
    assert top.url == "https://a.example/api/json.html"


def test_intent_boost_reorders_by_label(storage):
    off = BM25FRanker(storage, BM25FParams(intent_boost=0.0, package_boost=0.0))
    on = BM25FRanker(storage, BM25FParams(intent_boost=5.0, package_boost=0.0))
    assert on.search("json changes in 3.15")[0].label == "CHANGELOG"
    assert off.search("json api")[0].score != on.search("json api")[0].score


def test_named_package_boost_and_landing_page_package(storage):
    r = BM25FRanker(storage, BM25FParams(intent_boost=0.0, package_boost=10.0))
    assert r.package_of(5) == "flask"  # a landing page takes its host's package
    assert all(x.url.startswith("https://b.example") for x in r.search("flask json")[:2])


def test_label_filter(storage):
    assert {r.label for r in BM25FRanker(storage).search("json", label="HOWTO")} == {"HOWTO"}


def test_metrics():
    rel = {"a": 2, "b": 1}
    m = metrics(["x", "a", "b"], rel)
    assert m["rr"] == 0.5 and m["p@5"] == pytest.approx(0.4)
    assert metrics(["a", "b"], rel)["ndcg@10"] == pytest.approx(1.0)
    assert metrics(["x"], rel) == {"p@5": 0.0, "rr": 0.0, "ndcg@10": 0.0}


def test_paired_test():
    assert paired_test([0.0] * 10, [0.0] * 10) == 1.0
    assert paired_test([0.0] * 20, [1.0] * 20) < 0.001
