from focused_crawler.parser import parse_html
from tests.fakes import fixture_html

COURSE_URL = "https://www.cc.gatech.edu/courses/cs6675"


def test_extracts_title_meta_and_headings():
    page = parse_html(fixture_html("course.html"), COURSE_URL)
    assert page.title.startswith("CS 6675")
    assert page.meta["description"] == "Graduate course on internet-scale systems."
    assert (1, "CS 6675 Advanced Internet Computing Systems") in page.headings


def test_main_text_excludes_navigation():
    page = parse_html(fixture_html("course.html"), COURSE_URL)
    assert "3 credit hours" in page.text
    assert "Georgia Tech" not in page.text


def test_links_are_normalized_deduplicated_and_carry_context():
    page = parse_html(fixture_html("course.html"), COURSE_URL)
    urls = [link.url for link in page.links]
    assert "https://www.cc.gatech.edu/people/ling-liu" in urls  # utm stripped
    assert "https://www.cc.gatech.edu/courses/cs6250" in urls
    assert len(urls) == len(set(urls))
    cs6250 = next(link for link in page.links if link.url.endswith("cs6250"))
    assert cs6250.anchor == "CS 6250 page"
    assert "Prerequisite" not in cs6250.context and "CS 6250 or equivalent" in cs6250.context


def test_meta_robots_noindex_nofollow():
    page = parse_html(fixture_html("news.html"), "https://news.gatech.edu/dining")
    assert page.noindex
    assert page.links == []
