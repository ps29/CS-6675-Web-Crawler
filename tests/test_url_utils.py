import pytest

from focused_crawler.url_utils import in_scope, is_crawlable_resource, normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://WWW.GaTech.EDU", "https://www.gatech.edu/"),
        ("https://www.gatech.edu:443/a#frag", "https://www.gatech.edu/a"),
        ("http://cc.gatech.edu:8080/x", "http://cc.gatech.edu:8080/x"),
        ("https://a.gatech.edu/p?utm_source=x&b=2&a=1", "https://a.gatech.edu/p?a=1&b=2"),
        ("https://a.gatech.edu/p;jsessionid=ABC123?sid=9", "https://a.gatech.edu/p"),
        ("https://a.gatech.edu//x//y", "https://a.gatech.edu/x/y"),
    ],
)
def test_normalize(raw, expected):
    assert normalize_url(raw) == expected


def test_normalize_relative_against_base():
    assert (
        normalize_url("../people/", "https://cc.gatech.edu/a/b/")
        == "https://cc.gatech.edu/a/people/"
    )


@pytest.mark.parametrize("raw", ["mailto:x@gatech.edu", "javascript:void(0)", "ftp://x.edu/", ""])
def test_normalize_rejects_non_http(raw):
    assert normalize_url(raw) is None


def test_in_scope():
    assert in_scope("https://gatech.edu/", "gatech.edu")
    assert in_scope("https://www.cc.gatech.edu/x", "gatech.edu")
    assert not in_scope("https://notgatech.edu/", "gatech.edu")
    assert not in_scope("https://gatech.edu.evil.com/", "gatech.edu")


def test_crawlable_resource():
    assert is_crawlable_resource("https://gatech.edu/courses/cs6675")
    assert not is_crawlable_resource("https://gatech.edu/logo.PNG")
    assert not is_crawlable_resource("https://gatech.edu/syllabus.pdf")
