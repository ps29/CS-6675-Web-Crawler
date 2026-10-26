"""Rule-based page-type classifier and link-priority estimator (M1 baseline, Section 3.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .parser import ParsedPage

COURSE, FACULTY, LAB, OTHER = "COURSE", "FACULTY", "LAB", "OTHER"
LABELS = (COURSE, FACULTY, LAB, OTHER)

COURSE_CODE = re.compile(r"\b([A-Z]{2,4})[ \-]?(\d{4}[A-Z]?)\b")

URL_PATTERNS = {
    COURSE: re.compile(r"/(?:courses?|catalog|syllabus|syllabi|class(?:es)?|coursesaz)(?:/|$|-)"),
    FACULTY: re.compile(r"/(?:people|faculty|directory|profiles?|person|staff|~[a-z])"),
    LAB: re.compile(r"/(?:labs?|research|groups?|centers?|institutes?)(?:/|$|-)"),
}
CONTENT_CUES = {
    COURSE: (
        "credit hours",
        "prerequisite",
        "syllabus",
        "course description",
        "learning outcomes",
        "grading",
        "office hours",
    ),
    FACULTY: (
        "publications",
        "research interests",
        "research areas",
        "professor",
        "ph.d",
        "curriculum vitae",
        "biography",
    ),
    LAB: (
        "laboratory",
        "research group",
        "principal investigator",
        "lab members",
        "our research",
        "projects",
        "director",
    ),
}
LOW_VALUE_PATH = re.compile(
    r"/(?:news|events?|calendar|login|logout|sso|cas|cart|give|giving|donate|athletics|dining"
    r"|parking|jobs|careers|search|tag|feed|print|share)(?:/|$|-)"
)
ANCHOR_CUES = re.compile(
    r"\b(?:courses?|syllabus|catalog|faculty|people|professor|research|labs?|group|center"
    r"|publications|directory)\b",
    re.IGNORECASE,
)

URL_WEIGHT = 0.45
CUE_WEIGHT = 0.12
TITLE_CODE_WEIGHT = 0.35
MIN_CONFIDENCE = 0.35


@dataclass(frozen=True)
class Classification:
    label: str
    score: float
    scores: dict[str, float]


class RuleClassifier:
    def classify(self, page: ParsedPage) -> Classification:
        path = urlsplit(page.url).path.lower()
        head = f"{page.title} {page.heading_text}"
        body = f"{head} {page.text}".lower()

        scores: dict[str, float] = {}
        for label in (COURSE, FACULTY, LAB):
            s = URL_WEIGHT if URL_PATTERNS[label].search(path) else 0.0
            s += CUE_WEIGHT * sum(cue in body for cue in CONTENT_CUES[label])
            scores[label] = s
        if COURSE_CODE.search(head):
            scores[COURSE] += TITLE_CODE_WEIGHT
        if re.search(r"\b(?:lab|laboratory|group|center)\b", head, re.IGNORECASE):
            scores[LAB] += CUE_WEIGHT * 2
        if re.search(r"\bprofessor\b", head, re.IGNORECASE):
            scores[FACULTY] += CUE_WEIGHT * 2

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
    if COURSE_CODE.search(anchor):
        score += 0.1
    if parent_label != OTHER:
        score += 0.1
    return min(1.0, score)
