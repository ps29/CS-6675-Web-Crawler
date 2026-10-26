"""Python-documentation profile: page types, link priorities and fields for docs sites.

Targets Sphinx sites (docs.python.org, Read the Docs, most package docs) and MkDocs sites
(FastAPI, Typer, HTTPX). Labels: REFERENCE (API/module pages), TUTORIAL, HOWTO (guides and
topic pages), CHANGELOG (what's new, release notes) and OTHER.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .classifier import CUE_WEIGHT, MIN_CONFIDENCE, OTHER, URL_WEIGHT, Classification
from .parser import ParsedPage, clean

REFERENCE, TUTORIAL, HOWTO, CHANGELOG = "REFERENCE", "TUTORIAL", "HOWTO", "CHANGELOG"
LABELS = (REFERENCE, TUTORIAL, HOWTO, CHANGELOG, OTHER)

_END = r"(?:/|\.html|$|-|_|\d)"
URL_PATTERNS = {
    REFERENCE: re.compile(
        r"/(?:library|reference|ref|api|c-api|generated|modules?|autoapi|apidocs?)" + _END
    ),
    TUTORIAL: re.compile(
        r"/(?:tutorials?|intro|quickstart|getting[-_]?started|first[-_]steps|user[-_]?guide|user"
        r"|learn|beginners?)" + _END
    ),
    HOWTO: re.compile(
        r"/(?:howto|how-to|guides?|topics|patterns|recipes|cookbook|advanced|deployment|faq)" + _END
    ),
    CHANGELOG: re.compile(
        r"/(?:whatsnew|changelog|changes|release[-_]?notes|releases?|news|history)"
        + _END
        + r"|api[-_]changes|whats[-_]new"
    ),
}
CONTENT_CUES = {
    REFERENCE: (
        "parameters",
        "returns",
        "raises",
        "return type",
        "source code:",
        "attributes",
        "methods",
    ),
    TUTORIAL: (
        "tutorial",
        "let's",
        "you will",
        "you'll",
        "step 1",
        "next steps",
        "getting started",
    ),
    HOWTO: (
        "how to",
        "this guide",
        "this document describes",
        "recipe",
        "best practice",
        "you can use",
    ),
    CHANGELOG: (
        "release notes",
        "what's new",
        "changelog",
        "bug fixes",
        "new features",
        "backwards incompatible",
        "breaking changes",
    ),
}
TITLE_CUES = {
    TUTORIAL: re.compile(r"\btutorial|\bquickstart\b|\bgetting started\b", re.I),
    HOWTO: re.compile(r"\bhow ?to\b|\bguide\b|\bhowto\b", re.I),
    CHANGELOG: re.compile(r"what'?s new|changelog|release notes|\bchanges\b|\bhistory\b", re.I),
    REFERENCE: re.compile(r"^[A-Za-z_][\w.]*\s+[—–-]\s|\bAPI\b(?!\s+[Cc]hanges)|\breference\b"),
}
API_SELECTOR = (
    "dl.py, dl.function, dl.class, dl.method, dl.attribute, dl.exception, dl.data, "
    ".doc-object, .doc-heading"
)
VERSION_HEADING = re.compile(
    r"^(?:what'?s new in (?:python )?|version |release |v)?(\d+\.\d+(?:\.\d+)?)\b", re.I
)
LOW_VALUE_PATH = re.compile(
    r"/(?:genindex|search|_sources|_static|_images|_downloads|_modules|copyright|license|bugs"
    r"|download|contributing|code[-_]of[-_]conduct|sponsors?|funding)(?:/|\.html|$|-)"
)
ANCHOR_CUES = re.compile(
    r"\b(?:api|reference|modules?|library|tutorials?|guides?|how ?to|quickstart|user guide"
    r"|what's new|changelog|release notes|functions?|class(?:es)?)\b",
    re.IGNORECASE,
)
SYMBOL = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+(?:\(\))?$")
IDENTIFIER = re.compile(r"^[A-Za-z_][\w.]*$")

PACKAGE_HOSTS = {
    "docs.python.org": "python",
    "docs.djangoproject.com": "django",
    "docs.celeryq.dev": "celery",
}
HOST_NOISE = {"www", "docs", "doc"}
MAX_SYMBOLS = 200
MAX_SECTIONS = 20
MAX_VERSIONS = 20


def _api_blocks(page: ParsedPage) -> int:
    return len(page.soup.select(API_SELECTOR))


def _version_headings(page: ParsedPage) -> list[str]:
    seen: dict[str, None] = {}
    for _, text in page.headings:
        m = VERSION_HEADING.match(_strip_anchor(text))
        if m:
            seen.setdefault(m.group(1), None)
    return list(seen)


class PythonDocsClassifier:
    """Rule-based REFERENCE / TUTORIAL / HOWTO / CHANGELOG / OTHER labels."""

    def classify(self, page: ParsedPage) -> Classification:
        path = urlsplit(page.url).path.lower()
        h1 = next((h for level, h in page.headings if level == 1), "")
        head = f"{page.title} {page.heading_text}"
        body = f"{head} {page.text}".lower()

        scores: dict[str, float] = {}
        for label in (REFERENCE, TUTORIAL, HOWTO, CHANGELOG):
            s = URL_WEIGHT if URL_PATTERNS[label].search(path) else 0.0
            s += CUE_WEIGHT * sum(cue in body for cue in CONTENT_CUES[label])
            if TITLE_CUES[label].search(_strip_anchor(h1) or page.title):
                s += CUE_WEIGHT * 2
            scores[label] = s

        api = _api_blocks(page)
        scores[REFERENCE] += 0.35 if api >= 3 else 0.2 if api else 0.0
        if len(_version_headings(page)) >= 3:
            scores[CHANGELOG] += 0.35

        scores = {k: min(1.0, v) for k, v in scores.items()}
        best = max(scores, key=scores.get)
        if scores[best] < MIN_CONFIDENCE:
            return Classification(OTHER, 1.0 - scores[best], scores)
        return Classification(best, scores[best], scores)


def score_link(url: str, anchor: str, context: str, parent_label: str) -> float:
    """Crawl priority in [0, 1] for an outgoing link (higher is crawled first)."""
    path = urlsplit(url).path.lower()
    if LOW_VALUE_PATH.search(path):
        return 0.05
    score = 0.2
    if any(p.search(path) for p in URL_PATTERNS.values()):
        score += 0.4
    if ANCHOR_CUES.search(anchor):
        score += 0.2
    elif ANCHOR_CUES.search(context):
        score += 0.1
    if SYMBOL.match(anchor.strip()):
        score += 0.1
    if parent_label != OTHER:
        score += 0.1
    return min(1.0, score)


# --- field extraction ---------------------------------------------------------------

VERSION_IN_TITLE = (
    re.compile(r"\bv?(\d+(?:\.\d+)+\w*)\s+(?:documentation|docs|manual)\b", re.I),
    re.compile(r"(?:documentation|docs)\s*\(v?(\d[\w.]*)\)", re.I),
)
VERSION_IN_PATH = re.compile(r"/(?:en/|doc/)?(\d+(?:\.\d+)*|stable|latest|dev)/")


def _strip_anchor(text: str) -> str:
    """Sphinx and MkDocs append a permalink character to headings."""
    return text.rstrip(" ¶#").strip()


def package_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host in PACKAGE_HOSTS:
        return PACKAGE_HOSTS[host]
    labels = host.split(".")
    if host.endswith((".readthedocs.io", ".readthedocs.org")):
        return labels[0]
    for part in labels[:-1]:
        if part not in HOST_NOISE:
            return part.removeprefix("python-")
    return labels[0]


def doc_version(page: ParsedPage) -> str | None:
    for pattern in VERSION_IN_TITLE:
        if m := pattern.search(page.title):
            return m.group(1)
    m = VERSION_IN_PATH.search(urlsplit(page.url).path)
    return m.group(1) if m else None


def _title(page: ParsedPage) -> str:
    h1 = next((h for level, h in page.headings if level == 1), "")
    return _strip_anchor(h1) or re.split(r"\s+[—|]\s+", page.title)[0]


def _module(page: ParsedPage) -> str | None:
    tag = page.soup.find(id=re.compile(r"^module-"))
    if tag is not None:
        return tag["id"][len("module-") :]
    m = re.match(r"^([A-Za-z_][\w.]*)\s+[—–-]\s", _title(page))
    return m.group(1) if m else None


def _symbols(page: ParsedPage) -> list[str]:
    seen: dict[str, None] = {}
    for tag in page.soup.select("dl dt[id], .doc-heading[id]"):
        ident = tag["id"]
        if IDENTIFIER.match(ident) and not ident.startswith("module-"):
            seen.setdefault(ident, None)
    return list(seen)


def extract_doc_page(page: ParsedPage, label: str) -> dict:
    symbols = _symbols(page)
    fields = {
        "package": package_of(page.url),
        "kind": label.lower(),
        "title": _title(page),
        "version": doc_version(page),
        "module": _module(page) if label == REFERENCE else None,
        "symbols": symbols[:MAX_SYMBOLS],
        "n_symbols": len(symbols),
        "n_code_blocks": len(page.soup.find_all("pre")),
        "sections": [_strip_anchor(clean(h)) for lvl, h in page.headings if lvl == 2][
            :MAX_SECTIONS
        ],
    }
    if label == CHANGELOG:
        fields["versions"] = _version_headings(page)[:MAX_VERSIONS]
    return fields


def _extractor(label: str):
    return lambda page: extract_doc_page(page, label)


EXTRACTORS = {label: _extractor(label) for label in (REFERENCE, TUTORIAL, HOWTO, CHANGELOG)}
DOC_COLUMNS = ("package", "kind", "title", "version", "module", "n_symbols", "n_code_blocks")
STRUCTURED_TABLES = {
    label: ("doc_pages", (*DOC_COLUMNS, "symbols"))
    for label in (REFERENCE, TUTORIAL, HOWTO, CHANGELOG)
}
