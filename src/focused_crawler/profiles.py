"""Crawl profiles: which page types a crawl targets and how it classifies, scores and extracts.

`academic` is the M1 university-search design (COURSE / FACULTY / LAB). `python_docs` targets
Python documentation sites (REFERENCE / TUTORIAL / HOWTO / CHANGELOG). Labels are unique across
profiles, so one database schema and one search index serve both.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import classifier as academic
from . import python_docs
from .extractor import EXTRACTORS as ACADEMIC_EXTRACTORS
from .parser import ParsedPage


@dataclass(frozen=True)
class Profile:
    name: str
    labels: tuple[str, ...]
    make_classifier: Callable[[], object]
    score_link: Callable[[str, str, str, str], float]
    extractors: dict[str, Callable[[ParsedPage], dict]]


PROFILES = {
    "academic": Profile(
        "academic",
        academic.LABELS,
        academic.RuleClassifier,
        academic.score_link,
        ACADEMIC_EXTRACTORS,
    ),
    "python_docs": Profile(
        "python_docs",
        python_docs.LABELS,
        python_docs.PythonDocsClassifier,
        python_docs.score_link,
        python_docs.EXTRACTORS,
    ),
}
ALL_LABELS = tuple(dict.fromkeys(label for p in PROFILES.values() for label in p.labels))


def get_profile(name: str) -> Profile:
    return PROFILES[name]
