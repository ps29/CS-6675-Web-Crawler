# Term project: pipeline results (2026-11-15)

> **Small corpus, draft judgments.** Everything here uses the 149-page Python-docs crawl
> (`data/pydocs.db`) and the 48 draft judged queries in `config/eval_queries.yaml`, which a
> person has not reviewed yet. One run each, on the 2-vCPU cloud VM.

## What was connected

The M5 batch job now feeds the M1 search engine:

1. `python -m corpus_jobs.corpus crawldb data/pydocs.db --out data/project/pydocs.jsonl --min-tokens 1`
   builds the job's input from the crawl database.
2. `python -m corpus_jobs.run local data/project/pydocs.jsonl data/project/out/local`
   builds the index and the near-duplicate pairs (any engine can be used here).
3. `python -m corpus_jobs.load --source-db data/pydocs.db --corpus data/project/pydocs.jsonl
   --job-out data/project/out/local --out-db data/project/pydocs_m5.db --check`
   writes a search database with the job's index.
4. `fcrawl --db data/project/pydocs_m5.db search "json dumps" --ranker bm25f` searches it.

The crawl database keeps no page text, only positional postings, so step 1 rebuilds each page's
text as its stemmed token stream. The job therefore indexes the same terms as the M1 indexer.

## Does the job-built index equal the M1 index?

| | Postings |
|---|---|
| M1 index (source database) | 85,678 |
| Job-built index, loaded | 85,590 |
| Identical (same term, page and tf) | 85,590 |

The 88 postings that are missing are terms longer than 40 characters (hashes and long
concatenated dotted names), which the job tokenizer does not accept and the corpus builder
leaves out. 148 of the 149 pages are indexed; the other page has no text.

## Ranking (48 draft queries)

| Index | Ranker | P@5 | MRR | nDCG@10 |
|---|---|---|---|---|
| M1 | tf | 0.183 | 0.565 | 0.557 |
| M1 | bm25f+intent+package | 0.275 | 0.903 | 0.905 |
| Job-built | tf | 0.183 | 0.565 | 0.557 |
| Job-built | bm25f+intent+package | 0.275 | 0.903 | 0.905 |
| Job-built, duplicates collapsed | tf | 0.188 | 0.570 | 0.570 |
| Job-built, duplicates collapsed | bm25f+intent+package | 0.275 | 0.903 | 0.905 |

- The job-built index gives the same scores as the M1 index for all six ranker configurations
  (`results/project/ranking_eval_m1.json` and `ranking_eval_job.json`).
- Near-duplicate detection found one pair in this crawl, `fastapi.tiangolo.com/how-to/` and
  `fastapi.tiangolo.com/reference/` (estimated similarity 0.84), two hub pages. Collapsing it
  hides one page. The small change for `tf` comes from that one page; no test was run on it, and
  with one pair it is not evidence that collapsing duplicates improves ranking.

## Not done yet

- The corpus is not yet filtered to records verified by the M3 ledger.
- The M4 edges do not serve search results yet.
- A crawl with more near-duplicates is needed to measure the effect of collapsing them.
- Loading goes through SQLite one posting per row; it has only been run on this small corpus.
