"""End-to-end offline crawl over a small fixture site."""

import pytest

from focused_crawler.classifier import COURSE, FACULTY, LAB, OTHER
from focused_crawler.config import parse_config
from focused_crawler.crawler import Crawler
from focused_crawler.fetcher import Fetcher
from focused_crawler.ranker import TFRanker
from focused_crawler.storage import Storage
from tests.fakes import FakeClock, FakeResponse, FakeSession, fixture_html

ROOT = "https://www.cc.gatech.edu"
HOME = """<html><head><title>College of Computing</title></head><body><main>
<h1>College of Computing</h1>
<p><a href="/news/2026/award">News: faculty award</a></p>
<p><a href="/courses/cs6675">CS 6675 course</a></p>
<p><a href="/people/ling-liu">Faculty: Ling Liu</a></p>
<p><a href="/research/disl">Research labs</a></p>
<p><a href="/about">About</a></p>
<p><a href="https://www.example.com/elsewhere">Partner</a></p>
<p><a href="/files/handbook.pdf">Handbook</a></p>
<p><a href="/private/admin">Admin</a></p>
<p><a href="/old-course">Old course link</a></p>
</main></body></html>"""
ABOUT = "<html><head><title>About</title></head><body><main><p>About us.</p></main></body></html>"
NEWS = fixture_html("news.html").replace('<meta name="robots" content="noindex, nofollow">', "")

ROUTES = {
    f"{ROOT}/robots.txt": FakeResponse(text="User-agent: *\nDisallow: /private/\n"),
    f"{ROOT}/": HOME,
    f"{ROOT}/courses/cs6675": fixture_html("course.html"),
    f"{ROOT}/people/ling-liu": fixture_html("faculty.html"),
    f"{ROOT}/research/disl": fixture_html("lab.html"),
    f"{ROOT}/news/2026/award": NEWS,
    f"{ROOT}/about": ABOUT,
    f"{ROOT}/old-course": FakeResponse(url=f"{ROOT}/courses/cs6675", text="moved"),
}


def run_crawl(max_pages=50, **crawl):
    config = parse_config(
        {
            "domain": "gatech.edu",
            "seeds": [f"{ROOT}/"],
            "crawl": {"max_depth": 3, **crawl},
            "storage": {"db_path": ":memory:"},
        }
    )
    session = FakeSession(ROUTES)
    clock = FakeClock()
    storage = Storage(":memory:")
    fetcher = Fetcher(session=session, sleep=clock.sleep, clock=clock)
    stats = Crawler(config, storage, fetcher=fetcher).run(max_pages=max_pages)
    return stats, storage, session


@pytest.fixture(scope="module")
def crawl():
    stats, storage, session = run_crawl()
    yield stats, storage, session
    storage.close()


def test_stays_in_scope_and_skips_disallowed_and_binary(crawl):
    _, _, session = crawl
    assert not any("example.com" in c for c in session.calls)
    assert f"{ROOT}/private/admin" not in session.calls
    assert f"{ROOT}/files/handbook.pdf" not in session.calls


def test_labels_pages(crawl):
    stats, storage, _ = crawl
    labels = {p["url"]: p["label"] for p in storage.iter_pages()}
    assert labels[f"{ROOT}/courses/cs6675"] == COURSE
    assert labels[f"{ROOT}/people/ling-liu"] == FACULTY
    assert labels[f"{ROOT}/research/disl"] == LAB
    assert labels[f"{ROOT}/about"] == OTHER
    assert 0 < stats.useful_yield < 1


def test_academic_links_crawled_before_low_value(crawl):
    _, _, session = crawl
    pages = [c for c in session.calls if not c.endswith("robots.txt")]
    assert pages.index(f"{ROOT}/people/ling-liu") < pages.index(f"{ROOT}/about")
    assert pages.index(f"{ROOT}/courses/cs6675") < pages.index(f"{ROOT}/news/2026/award")


def test_redirect_to_seen_page_is_not_duplicated(crawl):
    _, storage, _ = crawl
    urls = [p["url"] for p in storage.iter_pages()]
    assert urls.count(f"{ROOT}/courses/cs6675") == 1
    assert f"{ROOT}/old-course" not in urls


def test_structured_tables_populated(crawl):
    _, storage, _ = crawl
    assert storage.structured_rows(COURSE)[0]["course_code"] == "CS 6675"
    assert storage.structured_rows(FACULTY)[0]["email"] == "lingliu@cc.gatech.edu"
    assert storage.structured_rows(LAB)[0]["pi"] == "Ling Liu"


def test_search_ranks_course_page_first(crawl):
    _, storage, _ = crawl
    results = TFRanker(storage).search("CS 6675")
    assert results[0].url == f"{ROOT}/courses/cs6675"
    assert results[0].label == COURSE
    faculty = TFRanker(storage).search("distributed systems", label=FACULTY)
    assert [r.url for r in faculty] == [f"{ROOT}/people/ling-liu"]


def test_page_budget_is_enforced():
    stats, storage, session = run_crawl(max_pages=2)
    assert stats.fetched == 2
    assert len([c for c in session.calls if not c.endswith("robots.txt")]) == 2
    storage.close()


def test_processing_error_does_not_stop_crawl(monkeypatch):
    import focused_crawler.crawler as crawler_mod

    real = crawler_mod.extract_fields

    def flaky(page, label, selectors=None):
        if label == LAB:
            raise RuntimeError("boom")
        return real(page, label, selectors)

    monkeypatch.setattr(crawler_mod, "extract_fields", flaky)
    stats, storage, _ = run_crawl()
    assert stats.errors == 1
    assert stats.labels[COURSE] == 1
    storage.close()
