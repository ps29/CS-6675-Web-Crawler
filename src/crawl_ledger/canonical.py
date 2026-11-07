"""Canonical, block-level page digests for audits of pages that change between fetches.

An exact hash of the raw page fails honest peers whenever a build timestamp, a CSRF token or an
analytics ID changes. Similarity hashing (SimHash) would tolerate that, but it also tolerates a
one-token malicious edit such as `pip install requests` -> `pip install reqeusts`. Instead, a page
is reduced to a deterministic, versioned list of content blocks (headings, paragraphs, code,
links; or the fields of a PyPI JSON document), each block is hashed, and the record commits to
the Merkle root of the block hashes. An auditor then learns exactly which blocks differ, and
whether any of them is *sensitive* (code, a link target, an install command, a version string,
a package-metadata field).

CANON_VERSION is part of every digest, so peers and auditors that disagree on the rules never
compare digests made by different rules."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from functools import cached_property

from bs4 import BeautifulSoup, Comment

from .merkle import merkle_root

CANON_VERSION = 1

HEADING, TEXT, CODE, LINK, FIELD = "heading", "text", "code", "link", "field"
SENSITIVE_KINDS = frozenset({CODE, LINK})

# Containers holding the main content of common documentation generators, best first.
MAIN_SELECTORS = (
    'div[role="main"]',  # Sphinx (classic, alabaster, furo, RTD theme)
    "main",
    "article",
    "div.md-content",  # MkDocs Material
    "div.document",
    "body",
)
# Page chrome and machinery that is not documentation content.
DROP_TAGS = ("script", "style", "noscript", "template", "iframe", "svg", "nav", "footer",
             "header", "aside", "form", "button", "input", "select", "meta", "link")  # fmt: skip
DROP_CLASSES = re.compile(
    r"(^|[-_ ])(headerlink|footer|sidebar|related|breadcrumbs?|edit-?this|last-?updated|"
    r"rst-versions|version-?switcher|admonition-todo|announcement|cookie|toc|feedback)($|[-_ ])",
    re.I,
)
BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "dt", "dd", "td", "th",
              "pre", "blockquote", "figcaption", "caption")  # fmt: skip

# Text that legitimately changes from build to build. A whole block matching one of these is
# volatile and left out of the canonical form.
VOLATILE_TEXT = (
    re.compile(r"^\s*(last\s+(updated|modified)|updated)\s+(on\s+)?.{0,60}$", re.I),
    re.compile(r"^\s*(©|\(c\)|copyright)\s.{0,120}$", re.I),
    re.compile(r"^\s*(built|created|generated)\s+(with|using|by)\s.{0,120}$", re.I),
    re.compile(r"^\s*found\s+a\s+bug\??.{0,200}$", re.I),
)
# Text that is security-relevant even inside an ordinary paragraph.
SENSITIVE_TEXT = re.compile(
    r"(pip3?\s+install|conda\s+install|uv\s+(pip\s+)?add|poetry\s+add|python\s+-m\s+pip|"
    r"curl\s|wget\s|https?://|\bv?\d+\.\d+(\.\d+)?\b)",
    re.I,
)
# PyPI JSON fields that change on every request or carry no documentation content.
VOLATILE_JSON = re.compile(r"^(last_serial|info\.downloads(\..*)?|urls\[\d+\]\.downloads)$")
SENSITIVE_JSON = re.compile(
    r"^(info\.(name|version|home_page|download_url|project_url|package_url|requires_dist.*|"
    r"project_urls\..*|docs_url)|urls\[\d+\]\.(url|filename|digests\..*))$"
)

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Block:
    kind: str
    text: str
    sensitive: bool

    def digest(self) -> str:
        blob = json.dumps([CANON_VERSION, self.kind, self.text], separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()


@dataclass(frozen=True)
class Digest:
    """What a record commits to: the canonicalizer version, block hashes and their root."""

    version: int
    blocks: tuple[Block, ...]

    @cached_property
    def hashes(self) -> list[str]:
        return [b.digest() for b in self.blocks]

    @cached_property
    def root(self) -> str:
        # Mix the version in, so an empty page under v1 and v2 still differ.
        return merkle_root([hashlib.sha256(f"canon-v{self.version}".encode()).hexdigest()]
                           + self.hashes)  # fmt: skip

    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _is_volatile(text: str) -> bool:
    return any(p.match(text) for p in VOLATILE_TEXT)


def html_blocks(content: bytes) -> list[Block]:
    soup = BeautifulSoup(content, "lxml")
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()  # comments carry build IDs and timestamps; the raw hash still covers them
    root = None
    for sel in MAIN_SELECTORS:
        root = soup.select_one(sel)
        if root is not None:
            break
    if root is None:
        return []
    # External scripts are kept as sensitive link blocks (query strings such as Sphinx's
    # ?v=<build hash> stripped), so an injected script still changes the digest; inline script
    # bodies, which carry nonces and analytics IDs, are dropped.
    scripts = sorted(
        {s["src"].split("?", 1)[0].strip() for s in soup.find_all("script", src=True)} - {""}
    )
    for tag in root.find_all(DROP_TAGS):
        tag.decompose()
    for tag in root.find_all(class_=DROP_CLASSES):
        tag.decompose()

    blocks: list[Block] = []
    covered: set[int] = set()  # ids of elements whose whole text a block already holds
    for el in root.find_all(BLOCK_TAGS):
        if el.find_parent("pre") is not None:
            continue  # a nested element of a code block; the <pre> covers it
        if el.name == "pre":
            covered.add(id(el))
            code = "\n".join(line.rstrip() for line in el.get_text().strip("\n").splitlines())
            if code:
                blocks.append(Block(CODE, code, True))
            continue
        if el.name in ("li", "dd", "td", "blockquote") and el.find(BLOCK_TAGS):
            continue  # a container: its inner blocks, and its own text below, are emitted
        covered.add(id(el))
        text = _norm(el.get_text(" "))
        if not text or _is_volatile(text):
            continue
        kind = HEADING if el.name.startswith("h") else TEXT
        blocks.append(Block(kind, text, bool(SENSITIVE_TEXT.search(text))))
    # Text outside every emitted block (a bare link, a container's own label) still counts:
    # one TEXT block per parent element, in document order.
    loose: dict[int, list[str]] = {}
    for string in root.find_all(string=True):
        parent = string.parent
        if any(id(p) in covered for p in [parent, *parent.parents]):
            continue
        if _norm(string):
            loose.setdefault(id(parent), []).append(str(string))
    for parts in loose.values():
        text = _norm(" ".join(parts))
        if not _is_volatile(text):
            blocks.append(Block(TEXT, text, bool(SENSITIVE_TEXT.search(text))))
    blocks.extend(Block(LINK, f"script:{src}", True) for src in scripts)
    for a in root.find_all("a", href=True):
        href = a["href"].strip()
        if href and not href.startswith(("#", "javascript:")):
            blocks.append(Block(LINK, href, True))
    return blocks


def _flatten(obj, path: str = ""):
    if isinstance(obj, dict):
        for k in sorted(obj):
            yield from _flatten(obj[k], f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flatten(v, f"{path}[{i}]")
    else:
        yield path, obj


def json_blocks(content: bytes) -> list[Block]:
    data = json.loads(content)
    blocks = []
    for path, value in _flatten(data):
        if VOLATILE_JSON.match(path):
            continue
        text = f"{path}={json.dumps(value, sort_keys=True)}"
        blocks.append(Block(FIELD, text, bool(SENSITIVE_JSON.match(path))))
    return blocks


def looks_like_json(content: bytes) -> bool:
    head = content.lstrip()[:1]
    return head in (b"{", b"[")


def digest(content: bytes) -> Digest:
    """CANONICALIZE(): deterministic block list for an HTML page or a JSON document."""
    if looks_like_json(content):
        try:
            return Digest(CANON_VERSION, tuple(json_blocks(content)))
        except ValueError:
            pass
    return Digest(CANON_VERSION, tuple(html_blocks(content)))


def simhash64(text: str) -> int:
    """64-bit SimHash (Charikar 2002) over word tokens; used only for the triage baseline."""
    weights = [0] * 64
    for token, n in Counter(re.findall(r"\w+", text.lower())).items():
        h = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
        for bit in range(64):
            weights[bit] += n if (h >> bit) & 1 else -n
    return sum(1 << bit for bit in range(64) if weights[bit] > 0)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")
