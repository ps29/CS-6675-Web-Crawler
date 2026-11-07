# Ranking: BM25F with intent and package boosts (results, 2026-11-08)

> **The judgments are a draft.** Claude wrote the 48 queries and their grades from the page
> titles and URLs of the 149-page index, after writing the intent rules, and no person has
> reviewed them. The numbers below are preliminary until the grades in
> `config/eval_queries.yaml` are reviewed and queries written without looking at the index
> are added.

The M1 ranker (`TFRanker`) scores `sum (1 + log tf) * log(1 + N/df)`, divided by `sqrt(|d|)`,
over one bag of words. `BM25FRanker` (`ranker.py`) works differently:

- **Three fields.** It scores the title, a *meta* field (package, module, API symbols and meta
  description, from `doc_pages`) and the body (the existing postings), with BM25F saturation and
  per-field length normalization.
- **Intent boost.** `query_intent` is a set of regex rules mapping a query to the labels it is
  probably after:
  - CHANGELOG: release notes, what's new, version numbers.
  - TUTORIAL or HOWTO: how to, install, tutorial, quickstart.
  - REFERENCE: api, class, function, dotted or snake_case names.

  A page whose label matches gets its score × 1.3.
- **Named-package boost.** When the query names a package, that package's pages get × 1.3. A
  landing page counts as its host's package.

The index contains only official documentation sites, so this boost cannot test official vs.
mirror sources. It tests only whether naming a package should favour that package's own docs.

All parameters were fixed before the evaluation and not tuned:

- k1 = 1.2
- field weights: title 3, meta 2, body 1
- b: 0.5 / 0.5 / 0.75
- both boosts: 0.3

## Method

`python -m focused_crawler.evaluate` runs each configuration on the 48 queries over
`data/pydocs.db`:

- 22 API or reference lookups
- 17 tutorial or how-to queries
- 9 changelog queries

Grades are 2 (answers the query) or 1 (related). P@5 counts grade ≥ 1; most queries have one
or two relevant pages, so the highest possible mean P@5 is 0.296. Configurations are compared by
a paired sign-flip randomization test on per-query scores (10,000 permutations, so the smallest
p-value is 0.0001). Raw output: `results/m1/ranking_eval.json`.

## Results

| Configuration | P@5 | MRR | nDCG@10 | p vs. tf (nDCG) | p vs. bm25f (nDCG) |
|---|---|---|---|---|---|
| tf (M1) | 0.183 | 0.565 | 0.557 | – | – |
| bm25 (body only) | 0.263 | 0.817 | 0.819 | 0.0001 | – |
| bm25f | 0.271 | 0.872 | 0.865 | 0.0001 | – |
| bm25f + intent | 0.271 | 0.883 | 0.889 | 0.0001 | 0.26 |
| bm25f + package | 0.275 | 0.892 | 0.881 | 0.0001 | 0.26 |
| bm25f + intent + package | 0.275 | 0.903 | 0.905 | 0.0001 | 0.09 |

nDCG@10 by the intent `query_intent` detected:

| Detected intent | Queries | tf | bm25f | bm25f + intent + package |
|---|---|---|---|---|
| CHANGELOG | 9 | 0.260 | 0.798 | 0.836 |
| TUTORIAL / HOWTO | 13 | 0.639 | 0.923 | 0.963 |
| REFERENCE | 11 | 0.603 | 0.860 | 0.924 |
| none | 15 | 0.632 | 0.857 | 0.882 |

## What this shows (on draft judgments)

- **Most of the gain comes from replacing the M1 score with BM25.** nDCG@10 rose from 0.557 to
  0.819. The M1 score's `1/sqrt(|d|)` normalization strongly favours short pages, such as
  landing pages.
- **Fields add a further gain,** to 0.865. This is mostly from symbols and module names in the
  meta field.
- **The boosts improved 12 queries and hurt 2,** reaching 0.905, but not significantly against
  plain BM25F (p = 0.09 combined).
  - "mark test functions" matches the REFERENCE rule through "function", so API pages beat the
    pytest how-to.
    The pytest how-to fell from first to fifth.
  - In "what's new in matplotlib 3.11", the CHANGELOG boost lifts matplotlib's landing page,
    which the classifier labels CHANGELOG, above the 3.11.0 API-changes page, which is labelled
    REFERENCE and was judged partly relevant. Both are label errors carried into ranking.
- **Version numbers are not searchable.** The tokenizer drops one-character tokens, so the "2.0"
  in "sqlalchemy 2.0 changelog" carries no weight, and the 2.0 changelog is not ranked first.

## Limits

- **Draft, possibly biased judgments.** Claude wrote both the intent rules and the judgments,
  after seeing the index. That favours the boosts.
- **Small sample.** 48 queries over 149 pages from 20 sites, so per-group numbers rest on 9-15
  queries.
- **The CLI default is unchanged.** `fcrawl search` still defaults to `--ranker tf`. Switch to
  `bm25f` once the judgments are reviewed.
