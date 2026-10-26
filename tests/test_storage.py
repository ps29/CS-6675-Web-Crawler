import json

import pytest

from focused_crawler.classifier import COURSE, FACULTY, OTHER
from focused_crawler.storage import Storage


@pytest.fixture
def db():
    with Storage(":memory:") as s:
        yield s


def add(db, url, label=OTHER, content_hash=None, fields=None):
    return db.upsert_page(
        url, "a.edu", 0, 200, "text/html", title=url, label=label,
        content_hash=content_hash, fields=fields,
    )  # fmt: skip


def test_upsert_is_idempotent_by_url(db):
    first = add(db, "https://a.edu/x")
    second = add(db, "https://a.edu/x", label=COURSE)
    assert first == second
    assert db.label_counts() == {COURSE: 1}


def test_duplicate_content_is_flagged_and_excluded_from_postings(db):
    a = add(db, "https://a.edu/a", content_hash="h1")
    b = add(db, "https://a.edu/b", content_hash="h1")
    db.replace_postings(a, {"crawl": [0, 3]})
    db.replace_postings(b, {"crawl": [1]})
    assert db.pages_by_id([b])[b]["duplicate_of"] == a
    rows = db.postings(["crawl"])
    assert [(r["page_id"], r["tf"]) for r in rows] == [(a, 2)]
    assert db.indexed_count() == 1


def test_postings_and_document_frequency(db):
    a = add(db, "https://a.edu/a")
    b = add(db, "https://a.edu/b")
    db.replace_postings(a, {"crawl": [0], "web": [1, 2]})
    db.replace_postings(b, {"web": [0]})
    assert db.document_frequency(["crawl", "web", "none"]) == {"crawl": 1, "web": 2}
    assert db.pages_by_id([a])[a]["doc_length"] == 3


def test_structured_rows_move_with_label(db):
    pid = add(db, "https://a.edu/p")
    db.replace_structured(pid, FACULTY, {"name": "Ada", "research_keywords": ["ml", "systems"]})
    row = db.structured_rows(FACULTY)[0]
    assert row["name"] == "Ada" and json.loads(row["research_keywords"]) == ["ml", "systems"]
    db.replace_structured(pid, COURSE, {"course_code": "CS 6675"})
    assert db.structured_rows(FACULTY) == []
    assert db.structured_rows(COURSE)[0]["course_code"] == "CS 6675"


def test_links_and_in_degree(db):
    a = add(db, "https://a.edu/a")
    b = add(db, "https://a.edu/b")
    db.replace_links(a, [("https://a.edu/c", "C"), ("https://a.edu/b", "B")])
    db.replace_links(b, [("https://a.edu/c", "C")])
    assert db.in_degree("https://a.edu/c") == 2
    db.replace_links(a, [])
    assert db.in_degree("https://a.edu/c") == 1


def test_summary_and_export(db):
    add(db, "https://a.edu/a", label=COURSE, fields={"course_code": "CS 1"})
    summary = db.summary()
    assert summary["pages"] == 1 and summary["labels"] == {COURSE: 1}
    exported = list(db.iter_pages())
    assert exported[0]["fields"] == {"course_code": "CS 1"}
