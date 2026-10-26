import pytest

from focused_crawler.classifier import COURSE, FACULTY, LAB, OTHER, RuleClassifier, score_link
from focused_crawler.parser import parse_html
from tests.fakes import fixture_html


@pytest.mark.parametrize(
    ("fixture", "url", "expected"),
    [
        ("course.html", "https://www.cc.gatech.edu/courses/cs6675", COURSE),
        ("faculty.html", "https://www.cc.gatech.edu/people/ling-liu", FACULTY),
        ("lab.html", "https://www.cc.gatech.edu/research/disl", LAB),
        ("news.html", "https://news.gatech.edu/2026/dining", OTHER),
    ],
)
def test_classifies_fixture_pages(fixture, url, expected):
    result = RuleClassifier().classify(parse_html(fixture_html(fixture), url))
    assert result.label == expected
    assert 0.0 <= result.score <= 1.0


def test_content_cues_work_without_url_hints():
    page = parse_html(fixture_html("faculty.html"), "https://www.gatech.edu/~lliu-home")
    assert RuleClassifier().classify(page).label == FACULTY


def test_link_priorities_prefer_academic_paths():
    academic = score_link("https://a.gatech.edu/people/x", "Faculty profile", "", OTHER)
    generic = score_link("https://a.gatech.edu/about", "About us", "", OTHER)
    low = score_link("https://a.gatech.edu/news/2026/x", "Faculty news", "", COURSE)
    assert academic > generic > low


def test_parent_label_boosts_link():
    url = "https://a.gatech.edu/page"
    assert score_link(url, "x", "", COURSE) > score_link(url, "x", "", OTHER)
