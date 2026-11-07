"""Baseline retrieval: TF-IDF over the inverted index (M1 Section 3.2, V = f(FTD, FTR))."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass

from .indexer import tokenize
from .storage import Storage


@dataclass(frozen=True)
class SearchResult:
    page_id: int
    url: str
    title: str
    label: str
    score: float
    fields: dict


class TFRanker:
    """score(d, q) = sum over query terms of (1 + log tf) * log(1 + N / df), / sqrt(|d|)."""

    def __init__(self, storage: Storage):
        self.storage = storage

    def search(self, query: str, k: int = 10, label: str | None = None) -> list[SearchResult]:
        terms = tokenize(query)
        if not terms:
            return []
        n_docs = max(self.storage.indexed_count(), 1)
        df = self.storage.document_frequency(terms)
        scores: dict[int, float] = defaultdict(float)
        for row in self.storage.postings(terms):
            idf = math.log(1 + n_docs / df[row["term"]])
            scores[row["page_id"]] += (1 + math.log(row["tf"])) * idf

        pages = self.storage.pages_by_id(scores)
        results = []
        for page_id, raw in scores.items():
            page = pages[page_id]
            if label and page["label"] != label:
                continue
            score = raw / math.sqrt(max(page["doc_length"], 1))
            results.append(
                SearchResult(
                    page_id,
                    page["url"],
                    page["title"] or "",
                    page["label"] or "",
                    score,
                    json.loads(page["fields_json"] or "{}"),
                )
            )
        results.sort(key=lambda r: (-r.score, r.url))
        return results[:k]


# --- BM25F with query intent and named-package boosts --------------------------------------

CHANGELOG, HOWTO, REFERENCE, TUTORIAL = "CHANGELOG", "HOWTO", "REFERENCE", "TUTORIAL"

INTENT_RULES = (  # (pattern, labels a query of this kind is looking for), first match wins
    (
        re.compile(
            r"\b(changelog|release notes?|what'?s new|changes? (in|for)|api changes|"
            r"deprecat\w*|migrat\w*)\b|\b\d+\.\d+(\.\d+)?\b",
            re.I,
        ),
        {CHANGELOG},
    ),  # fmt: skip
    (
        re.compile(
            r"\bhow (to|do i|can i)\b|\b(install\w*|deploy\w*|upgrad\w*|configur\w*|"
            r"set ?up|tutorial|quick ?start|getting started|first steps|intro\w*|guide|"
            r"basics|example\w*)\b",
            re.I,
        ),
        {TUTORIAL, HOWTO},
    ),  # fmt: skip
    (
        re.compile(
            r"\b(api|reference|class|function|method|parameters?|signature|module|"
            r"attribute)s?\b|\b\w+\.\w+\b|\b[a-z]+_[a-z_]+\b|\b[A-Z][a-z]+[A-Z]\w*\b"
        ),
        {REFERENCE},
    ),  # fmt: skip
)


def query_intent(query: str) -> frozenset[str]:
    """The page labels a query is most likely after (empty when no rule matches)."""
    for pattern, labels in INTENT_RULES:
        if pattern.search(query):
            return frozenset(labels)
    return frozenset()


@dataclass(frozen=True)
class BM25FParams:
    k1: float = 1.2
    weights: tuple[tuple[str, float], ...] = (("title", 3.0), ("meta", 2.0), ("body", 1.0))
    b: tuple[tuple[str, float], ...] = (("title", 0.5), ("meta", 0.5), ("body", 0.75))
    intent_boost: float = 0.3  # score x (1 + boost) when the page's label matches the intent
    package_boost: float = 0.3  # score x (1 + boost) when the query names the page's package


class BM25FRanker:
    """BM25F (Robertson, Zaragoza and Taylor 2004, simple variant) over three fields:

    - title: the page title;
    - meta: package, module, API symbols and meta description (the doc_pages fields);
    - body: the indexed text (title, headings and text; the existing postings).

    tf~(t, d) = sum_f w_f * tf_f / (1 - b_f + b_f * len_f / avglen_f)
    score(d, q) = sum_t idf(t) * tf~ / (k1 + tf~),  idf = log(1 + (N - df + 0.5) / (df + 0.5))

    The score is then multiplied by (1 + intent_boost) when the page's label is one the query
    asks for (`query_intent`), and by (1 + package_boost) when the query names the page's
    package. Either boost can be set to 0 to switch it off."""

    def __init__(self, storage: Storage, params: BM25FParams | None = None):
        self.storage = storage
        self.p = params or BM25FParams()
        self.w = dict(self.p.weights)
        self.b = dict(self.p.b)
        rows = storage.conn.execute(
            """SELECT g.id, g.url, g.host, g.title, g.description, g.label, g.doc_length,
                      g.fields_json, d.package, d.module, d.symbols
               FROM pages g LEFT JOIN doc_pages d ON d.page_id = g.id
               WHERE g.doc_length > 0 AND g.duplicate_of IS NULL"""
        ).fetchall()
        self.pages = {r["id"]: r for r in rows}
        self.fields: dict[int, dict[str, dict[str, int]]] = {}
        host_pkgs: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in rows:
            symbols = " ".join(json.loads(r["symbols"] or "[]"))
            meta = " ".join(x or "" for x in (r["package"], r["module"], symbols, r["description"]))
            self.fields[r["id"]] = {"title": _counts(tokenize(r["title"] or "")),
                                    "meta": _counts(tokenize(meta))}  # fmt: skip
            if r["package"]:
                host_pkgs[r["host"]][r["package"].lower()] += 1
        # A page without a package (a landing page) belongs to its host's usual package.
        self.host_package = {h: max(c, key=c.get) for h, c in host_pkgs.items()}
        self.packages = set(self.host_package.values())
        n = max(len(rows), 1)
        self.avg = {
            "title": sum(sum(f["title"].values()) for f in self.fields.values()) / n or 1.0,
            "meta": sum(sum(f["meta"].values()) for f in self.fields.values()) / n or 1.0,
            "body": sum(r["doc_length"] for r in rows) / n or 1.0,
        }

    def package_of(self, page_id: int) -> str | None:
        r = self.pages[page_id]
        return (r["package"] or "").lower() or self.host_package.get(r["host"])

    def named_packages(self, query: str) -> set[str]:
        words = set(re.findall(r"[a-z0-9]+", query.lower()))
        return {p for p in self.packages if p in words}

    def search(self, query: str, k: int = 10, label: str | None = None) -> list[SearchResult]:
        terms = list(dict.fromkeys(tokenize(query)))
        if not terms:
            return []
        body: dict[int, dict[str, int]] = defaultdict(dict)
        for row in self.storage.postings(terms):
            if row["page_id"] in self.pages:
                body[row["page_id"]][row["term"]] = row["tf"]
        candidates = set(body)
        for pid, f in self.fields.items():
            if any(t in f["title"] or t in f["meta"] for t in terms):
                candidates.add(pid)
        df = {t: sum(1 for pid in candidates if self._has(pid, t, body)) for t in terms}
        n = len(self.pages)
        intent = query_intent(query)
        named = self.named_packages(query)
        results = []
        for pid in candidates:
            page = self.pages[pid]
            if label and page["label"] != label:
                continue
            lens = {"title": sum(self.fields[pid]["title"].values()),
                    "meta": sum(self.fields[pid]["meta"].values()),
                    "body": page["doc_length"]}  # fmt: skip
            score = 0.0
            for t in terms:
                tfs = {"title": self.fields[pid]["title"].get(t, 0),
                       "meta": self.fields[pid]["meta"].get(t, 0),
                       "body": body[pid].get(t, 0)}  # fmt: skip
                tf = sum(
                    self.w[f] * tfs[f] / (1 - self.b[f] + self.b[f] * lens[f] / self.avg[f])
                    for f in tfs
                    if tfs[f]
                )
                if tf:
                    idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                    score += idf * tf / (self.p.k1 + tf)
            if page["label"] in intent:
                score *= 1 + self.p.intent_boost
            if named and self.package_of(pid) in named:
                score *= 1 + self.p.package_boost
            results.append(
                SearchResult(
                    pid,
                    page["url"],
                    page["title"] or "",
                    page["label"] or "",
                    score,
                    json.loads(page["fields_json"] or "{}"),
                )  # fmt: skip
            )
        results.sort(key=lambda r: (-r.score, r.url))
        return results[:k]

    def _has(self, pid: int, term: str, body: dict) -> bool:
        f = self.fields[pid]
        return term in body.get(pid, {}) or term in f["title"] or term in f["meta"]


def _counts(tokens: list[str]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for t in tokens:
        out[t] += 1
    return dict(out)


RANKERS = {"tf": TFRanker, "bm25f": BM25FRanker}
