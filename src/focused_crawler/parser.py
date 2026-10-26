"""HTML parsing: title, meta tags, headings, main text, and links with anchor context."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from .url_utils import normalize_url

BOILERPLATE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "form")
CHROME_TAGS = ("nav", "header", "footer", "aside")
BLOCK_TAGS = (
    "p", "div", "li", "ul", "ol", "dt", "dd", "tr", "td", "th", "br", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table",
)  # fmt: skip
CONTEXT_CHARS = 200
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Link:
    url: str
    anchor: str
    context: str


@dataclass
class ParsedPage:
    url: str
    title: str
    meta: dict[str, str]
    headings: list[tuple[int, str]]
    text: str
    lines: str
    links: list[Link]
    soup: BeautifulSoup = field(repr=False)
    noindex: bool = False

    @property
    def heading_text(self) -> str:
        return " ".join(h for _, h in self.headings)


def clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def parse_html(html: str, url: str) -> ParsedPage:
    soup = BeautifulSoup(html, "lxml")

    meta: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        key = (tag.get("name") or tag.get("property") or "").strip().lower()
        if key and tag.get("content"):
            meta[key] = clean(tag["content"])
    robots = meta.get("robots", "").lower()

    base_tag = soup.find("base", href=True)
    base = normalize_url(base_tag["href"], url) if base_tag else url
    base = base or url

    title = clean(soup.title.get_text()) if soup.title else ""
    links = [] if "nofollow" in robots else _extract_links(soup, base)

    for tag in soup(BOILERPLATE_TAGS):
        tag.decompose()
    headings = [
        (int(h.name[1]), clean(h.get_text(" ")))
        for h in soup.find_all(["h1", "h2", "h3", "h4"])
        if clean(h.get_text(" "))
    ]
    lines = _main_lines(soup)

    return ParsedPage(
        url=url,
        title=title,
        meta=meta,
        headings=headings,
        text=clean(lines),
        lines=lines,
        links=links,
        soup=soup,
        noindex="noindex" in robots,
    )


def _extract_links(soup: BeautifulSoup, base: str) -> list[Link]:
    links: list[Link] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        norm = normalize_url(a["href"], base)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        anchor = clean(a.get_text(" ")) or clean(a.get("title", ""))
        parent = a.find_parent(["li", "p", "td", "div", "section"]) or a
        links.append(Link(norm, anchor, clean(parent.get_text(" "))[:CONTEXT_CHARS]))
    return links


def _main_lines(soup: BeautifulSoup) -> str:
    """Main content, one line per block element. Prefer <main>/<article> over the body."""
    root = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"})
    if root is None:
        root = soup.body or soup
        for tag in root.find_all(CHROME_TAGS):
            tag.decompose()
    for tag in root.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    lines = (clean(line) for line in root.get_text(" ").split("\n"))
    return "\n".join(line for line in lines if line)
