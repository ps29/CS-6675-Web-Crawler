from focused_crawler.indexer import content_hash, term_positions, tokenize
from focused_crawler.parser import parse_html
from tests.fakes import fixture_html


def test_tokenize_stems_and_drops_stopwords():
    assert tokenize("The crawlers are crawling systems") == ["crawler", "crawl", "system"]


def test_tokenize_joins_course_codes():
    tokens = tokenize("Take CS 6675 or CS-6250.")
    assert "cs6675" in tokens and "cs6250" in tokens
    assert "6675" in tokens


def test_term_positions_cover_title_and_body():
    page = parse_html(fixture_html("course.html"), "https://www.cc.gatech.edu/courses/cs6675")
    positions = term_positions(page)
    assert positions["cs6675"][0] == 0  # title comes first
    assert len(positions["crawl"]) >= 1
    assert all(p == sorted(p) for p in positions.values())


def test_content_hash_is_stable():
    html = fixture_html("lab.html")
    a = parse_html(html, "https://a.gatech.edu/lab")
    b = parse_html(html, "https://a.gatech.edu/lab?print=1")
    assert content_hash(a) == content_hash(b)
