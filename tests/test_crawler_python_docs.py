"""End-to-end offline crawl with the python_docs profile over two fixture docs sites."""

import json

import pytest

from focused_crawler.config import parse_config
from focused_crawler.crawler import Crawler
from focused_crawler.fetcher import Fetcher
from focused_crawler.python_docs import CHANGELOG, HOWTO, REFERENCE, TUTORIAL
from focused_crawler.ranker import TFRanker
from focused_crawler.storage import Storage
from tests.fakes import FakeClock, FakeResponse, FakeSession, fixture_html

PY = "https://docs.python.org/3"
FLASK = "https://flask.palletsprojects.com/en/stable"
HOME = f"""<html><head><title>3.14.0 Documentation</title></head><body>
<div role="main"><h1>Python 3.14.0 documentation</h1>
<p><a href="library/json.html">json — JSON encoder and decoder</a></p>
<p><a href="tutorial/introduction.html">Tutorial: an informal introduction</a></p>
<p><a href="howto/logging.html">Logging HOWTO</a></p>
<p><a href="copyright.html">Copyright</a></p>
<p><a href="genindex.html">Index</a></p>
<p><a href="_sources/index.rst.txt">Show source</a></p>
<p><a href="https://docs.python.org/3.12/library/json.html">Python 3.12 json</a></p>
<p><a href="https://docs.python.org/fr/3/library/json.html">Français</a></p>
<p><a href="{FLASK}/changes/">Flask changes</a></p>
<p><a href="https://pypi.org/project/flask/">Flask on PyPI</a></p>
</div></body></html>"""

ROUTES = {
    "https://docs.python.org/robots.txt": FakeResponse(text="User-agent: *\nAllow: /\n"),
    "https://flask.palletsprojects.com/robots.txt": FakeResponse(text=""),
    f"{PY}/": HOME,
    f"{PY}/library/json.html": fixture_html("pydocs_json.html"),
    f"{PY}/tutorial/introduction.html": fixture_html("pydocs_tutorial.html"),
    f"{PY}/howto/logging.html": fixture_html("pydocs_howto.html"),
    f"{PY}/copyright.html": fixture_html("pydocs_copyright.html"),
    f"{PY}/genindex.html": "<html><title>Index</title><body>index</body></html>",
    f"{FLASK}/changes/": fixture_html("pydocs_changes.html"),
}


@pytest.fixture(scope="module")
def crawl():
    config = parse_config(
        {
            "profile": "python_docs",
            "domains": ["docs.python.org/3/", "flask.palletsprojects.com/en/stable/"],
            "seeds": [f"{PY}/"],
            "exclude": ["/(?:genindex|search)(?:\\.html)?$", "/_sources/"],
            "crawl": {"max_depth": 3},
        }
    )
    session = FakeSession(ROUTES)
    clock = FakeClock()
    storage = Storage(":memory:")
    fetcher = Fetcher(session=session, sleep=clock.sleep, clock=clock)
    stats = Crawler(config, storage, fetcher=fetcher).run()
    yield stats, storage, session
    storage.close()


def test_scope_prefixes_and_excludes(crawl):
    _, _, session = crawl
    calls = [c for c in session.calls if not c.endswith("robots.txt")]
    assert f"{FLASK}/changes/" in calls  # second docs site followed
    assert not any("3.12" in c or "/fr/" in c or "pypi.org" in c for c in calls)
    assert not any("genindex" in c or "_sources" in c for c in calls)


def test_labels_and_yield(crawl):
    stats, storage, _ = crawl
    labels = {p["url"]: p["label"] for p in storage.iter_pages()}
    assert labels[f"{PY}/library/json.html"] == REFERENCE
    assert labels[f"{PY}/tutorial/introduction.html"] == TUTORIAL
    assert labels[f"{PY}/howto/logging.html"] == HOWTO
    assert labels[f"{FLASK}/changes/"] == CHANGELOG
    assert labels[f"{PY}/copyright.html"] == "OTHER"
    assert 0 < stats.useful_yield < 1


def test_docs_crawled_before_chrome(crawl):
    _, _, session = crawl
    calls = [c for c in session.calls if not c.endswith("robots.txt")]
    assert calls.index(f"{PY}/library/json.html") < calls.index(f"{PY}/copyright.html")


def test_doc_pages_table(crawl):
    _, storage, _ = crawl
    rows = {r["kind"]: r for r in storage.structured_rows(REFERENCE)}
    assert set(rows) == {"reference", "tutorial", "howto", "changelog"}
    ref = rows["reference"]
    assert (ref["package"], ref["module"], ref["n_symbols"]) == ("python", "json", 5)
    assert "json.dumps" in json.loads(ref["symbols"])
    assert rows["changelog"]["package"] == "flask"


def test_symbol_search_finds_module_page(crawl):
    _, storage, _ = crawl
    results = TFRanker(storage).search("json.dumps")
    assert results[0].url == f"{PY}/library/json.html"
    assert results[0].fields["module"] == "json"
    assert [r.label for r in TFRanker(storage).search("logging", label=HOWTO)] == [HOWTO]
