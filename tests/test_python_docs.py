"""Python-documentation profile: classification, link scoring and field extraction."""

import pytest

from focused_crawler.classifier import OTHER
from focused_crawler.indexer import tokenize
from focused_crawler.parser import parse_html
from focused_crawler.python_docs import (
    CHANGELOG,
    HOWTO,
    REFERENCE,
    TUTORIAL,
    PythonDocsClassifier,
    extract_doc_page,
    package_of,
    score_link,
)
from tests.fakes import fixture_html

PY = "https://docs.python.org/3"
CASES = {
    "json": ("pydocs_json.html", f"{PY}/library/json.html"),
    "tutorial": ("pydocs_tutorial.html", f"{PY}/tutorial/introduction.html"),
    "howto": ("pydocs_howto.html", f"{PY}/howto/logging.html"),
    "changes": ("pydocs_changes.html", "https://flask.palletsprojects.com/en/stable/changes/"),
    "mkdocs": ("pydocs_mkdocs_api.html", "https://fastapi.tiangolo.com/reference/fastapi/"),
    "copyright": ("pydocs_copyright.html", f"{PY}/copyright.html"),
}


def page(name):
    fixture, url = CASES[name]
    return parse_html(fixture_html(fixture), url)


@pytest.mark.parametrize(
    "name, label",
    [
        ("json", REFERENCE),
        ("tutorial", TUTORIAL),
        ("howto", HOWTO),
        ("changes", CHANGELOG),
        ("mkdocs", REFERENCE),
        ("copyright", OTHER),
    ],
)
def test_classifies_doc_page_types(name, label):
    assert PythonDocsClassifier().classify(page(name)).label == label


def test_changelog_detected_from_version_headings_without_url_cue():
    html = fixture_html("pydocs_changes.html")
    result = PythonDocsClassifier().classify(parse_html(html, "https://x.org/en/stable/notes/"))
    assert result.label == CHANGELOG


def test_reference_fields_from_sphinx():
    fields = extract_doc_page(page("json"), REFERENCE)
    assert fields["package"] == "python"
    assert fields["kind"] == "reference"
    assert fields["module"] == "json"
    assert fields["title"] == "json — JSON encoder and decoder"
    assert fields["version"] == "3.14.0"
    assert fields["symbols"] == [
        "json.dump",
        "json.dumps",
        "json.loads",
        "json.JSONDecoder",
        "json.JSONDecoder.decode",
    ]
    assert fields["n_symbols"] == 5
    assert fields["n_code_blocks"] == 1
    assert fields["sections"] == ["Basic Usage", "Encoders and Decoders"]


def test_reference_fields_from_mkdocstrings():
    fields = extract_doc_page(page("mkdocs"), REFERENCE)
    assert fields["package"] == "fastapi"
    assert fields["symbols"] == ["fastapi.FastAPI", "fastapi.FastAPI.get"]
    assert fields["title"] == "FastAPI class"


def test_changelog_fields():
    fields = extract_doc_page(page("changes"), CHANGELOG)
    assert fields["package"] == "flask"
    assert fields["version"] == "3.1.x"
    assert fields["versions"] == ["3.1.1", "3.1.0", "3.0.3"]
    assert fields["module"] is None


@pytest.mark.parametrize(
    "url, package",
    [
        ("https://docs.python.org/3/library/os.html", "python"),
        ("https://requests.readthedocs.io/en/latest/", "requests"),
        ("https://numpy.org/doc/stable/", "numpy"),
        ("https://pandas.pydata.org/docs/", "pandas"),
        ("https://docs.djangoproject.com/en/6.1/", "django"),
        ("https://www.python-httpx.org/", "httpx"),
        ("https://docs.celeryq.dev/en/stable/", "celery"),
        ("https://jinja.palletsprojects.com/en/stable/", "jinja"),
    ],
)
def test_package_of(url, package):
    assert package_of(url) == package


def test_link_scoring_prefers_docs_over_chrome():
    api = score_link(f"{PY}/library/json.html", "json — JSON encoder", "", REFERENCE)
    symbol = score_link(f"{PY}/library/os.path.html", "os.path", "", OTHER)
    chrome = score_link(f"{PY}/genindex.html", "Index", "", REFERENCE)
    plain = score_link(f"{PY}/about.html", "About these documents", "", OTHER)
    assert api > plain > chrome
    assert symbol > plain


def test_dotted_names_are_searchable_as_one_token():
    assert "jsondump" in tokenize("call json.dumps here")
    assert tokenize("json.dumps")[-1] == "jsondump"


def test_matplotlib_api_changes_is_changelog_and_manual_version():
    html = (
        "<html><head><title>API Changes for 3.5.3 — Matplotlib 3.11.2 documentation</title>"
        "</head><body><div role='main'><h1>API Changes for 3.5.3</h1>"
        "<p>Behaviour changes.</p></div></body></html>"
    )
    url = "https://matplotlib.org/stable/api/prev_api_changes/api_changes_3.5.3.html"
    assert PythonDocsClassifier().classify(parse_html(html, url)).label == CHANGELOG
    numpy = parse_html(
        "<title>NumPy quickstart — NumPy v2.5 Manual</title>",
        "https://numpy.org/doc/stable/user/quickstart.html",
    )
    assert extract_doc_page(numpy, TUTORIAL)["version"] == "2.5"
