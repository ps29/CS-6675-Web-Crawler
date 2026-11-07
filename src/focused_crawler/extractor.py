"""Structured-field extraction per page type, plus user-configured CSS selectors."""

from __future__ import annotations

import re

from .classifier import COURSE, COURSE_CODE, FACULTY, LAB
from .parser import ParsedPage, clean

NAME = r"((?:Dr\.[ \t]+)?[A-Z][a-zA-Z.'\-]+(?:[ \t]+[A-Z][a-zA-Z.'\-]+){1,3})"
INSTRUCTOR = re.compile(r"\b(?:Instructors?|Taught by)[ \t]*[:\-][ \t]*" + NAME)
PI = re.compile(
    r"\b(?:Principal Investigator|Director|Faculty Lead|Lab Director|PI)[ \t]*[:\-][ \t]*" + NAME
)
CREDITS = re.compile(r"(\d+(?:\.\d+)?)\s*credit hours?", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
POSITION = re.compile(
    r"\b((?:Distinguished |Regents'? |Associate |Assistant |Adjunct |Visiting )*"
    r"(?:Professor|Lecturer|Research Scientist|Instructor)(?: of [A-Z][\w ]+)?)"
)
DEPARTMENT = re.compile(r"\b((?:School|Department|College) of [A-Z][\w&,]*(?: [A-Z&][\w&,]*){0,4})")
RESEARCH_HEADING = re.compile(r"research (?:interests|areas|topics)|areas of expertise", re.I)
TITLE_SEPARATORS = re.compile(r"\s+[|\-–—:]\s+")
MAX_KEYWORDS = 15


def _primary_title(page: ParsedPage) -> str:
    h1 = next((h for level, h in page.headings if level == 1), "")
    return h1 or TITLE_SEPARATORS.split(page.title)[0]


def _course_codes(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for dept, num in COURSE_CODE.findall(text):
        seen.setdefault(f"{dept} {num}", None)
    return list(seen)


def _research_keywords(page: ParsedPage) -> list[str]:
    for heading in page.soup.find_all(["h2", "h3", "h4", "strong", "dt"]):
        if not RESEARCH_HEADING.search(heading.get_text()):
            continue
        sibling = heading.find_next_sibling(["ul", "ol", "p", "dd", "div"])
        if sibling is None:
            continue
        items = [clean(li.get_text(" ")) for li in sibling.find_all("li")]
        if not items:
            items = re.split(r"[;,]|\band\b", clean(sibling.get_text(" ")))
        keywords = [k.strip(" .") for k in items if 1 < len(k.strip(" .")) <= 60]
        return keywords[:MAX_KEYWORDS]
    return []


def _first(pattern: re.Pattern, text: str) -> str | None:
    m = pattern.search(text)
    return clean(m.group(1)).strip(" ,;.") if m else None


def extract_course(page: ParsedPage) -> dict:
    head = f"{page.title} {page.heading_text}"
    codes = _course_codes(head) or _course_codes(page.text)
    return {
        "course_code": codes[0] if codes else None,
        "course_codes": _course_codes(f"{head} {page.text}")[:20],
        "title": _primary_title(page),
        "instructor": _first(INSTRUCTOR, page.lines),
        "credit_hours": _first(CREDITS, page.text),
    }


def extract_faculty(page: ParsedPage) -> dict:
    mailto = page.soup.select_one("a[href^='mailto:']")
    email = mailto["href"][7:].split("?")[0] if mailto else None
    if not email:
        m = EMAIL.search(page.text)
        email = m.group(0) if m else None
    return {
        "name": _primary_title(page),
        "position": _first(POSITION, page.lines),
        "department": _first(DEPARTMENT, page.lines),
        "email": email,
        "research_keywords": _research_keywords(page),
    }


def extract_lab(page: ParsedPage) -> dict:
    return {
        "name": _primary_title(page),
        "pi": _first(PI, page.lines),
        "department": _first(DEPARTMENT, page.lines),
    }


EXTRACTORS = {COURSE: extract_course, FACULTY: extract_faculty, LAB: extract_lab}


def apply_selectors(page: ParsedPage, selectors: dict[str, str]) -> dict:
    """Each selector is 'css' (element text) or 'css@attr' (attribute value)."""
    out: dict[str, str | list[str]] = {}
    for field_name, spec in selectors.items():
        css, _, attr = spec.partition("@")
        values = [
            clean(el.get(attr, "")) if attr else clean(el.get_text(" "))
            for el in page.soup.select(css)
        ]
        values = [v for v in values if v]
        if values:
            out[field_name] = values[0] if len(values) == 1 else values
    return out


def extract_fields(
    page: ParsedPage,
    label: str,
    selectors: dict[str, dict[str, str]] | None = None,
    extractors: dict | None = None,
) -> dict:
    """Built-in fields for the label, overridden by any matching configured selectors.

    `extractors` maps label -> extractor (default: the academic ones)."""
    extractors = EXTRACTORS if extractors is None else extractors
    fields = extractors[label](page) if label in extractors else {}
    selectors = selectors or {}
    for key in ("*", label):
        if key in selectors:
            fields.update(apply_selectors(page, selectors[key]))
    return fields
