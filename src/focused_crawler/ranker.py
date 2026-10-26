"""Baseline retrieval: TF-IDF over the inverted index (M1 Section 3.2, V = f(FTD, FTR))."""

from __future__ import annotations

import json
import math
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
