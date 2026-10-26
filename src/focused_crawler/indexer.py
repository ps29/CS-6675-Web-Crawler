"""Tokenize, stem and build positional posting lists (the inverted index of the M1 design)."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from nltk.stem import PorterStemmer

from .classifier import COURSE_CODE
from .parser import ParsedPage

TOKEN = re.compile(r"[a-z0-9]+")
DOTTED = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b")
STOPWORDS = frozenset(
    """a about above after again all also am an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from further
    had has have having he her here hers him his how i if in into is it its itself just me more
    most my no nor not now of off on once only or other our out over own same she should so some
    such than that the their them then there these they this those through to too under until up
    very was we were what when where which while who whom why will with would you your""".split()
)
_stemmer = PorterStemmer()
_stem_cache: dict[str, str] = {}


def stem(token: str) -> str:
    if token not in _stem_cache:
        _stem_cache[token] = _stemmer.stem(token)
    return _stem_cache[token]


def tokenize(text: str) -> list[str]:
    """Lowercased, stop-word-free, stemmed tokens. "CS 6675" also yields the token "cs6675",
    and a dotted name such as "json.dumps" also yields "jsondumps"."""
    text = COURSE_CODE.sub(lambda m: f"{m.group(1)}{m.group(2)} {m.group(1)} {m.group(2)}", text)
    text = DOTTED.sub(lambda m: f"{m.group(0)} {re.sub(r'[._]', '', m.group(0))}", text)
    return [stem(t) for t in TOKEN.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


def term_positions(page: ParsedPage) -> dict[str, list[int]]:
    positions: dict[str, list[int]] = defaultdict(list)
    for i, term in enumerate(tokenize(f"{page.title} {page.heading_text} {page.text}")):
        positions[term].append(i)
    return dict(positions)


def content_hash(page: ParsedPage) -> str:
    return hashlib.sha1(page.text.encode("utf-8")).hexdigest()
