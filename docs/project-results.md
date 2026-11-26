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

## Crawler: sequential against async per-host queues (2026-11-15)

> **A local test web, not real sites.** 20 test sites on 127.0.0.1 (one port each), 60 pages of
> 30 KB per site, every response delayed by 0.15 to 0.45 s (mean 0.3 s). The speed-up depends on
> these choices. 5 runs per configuration on the 2-vCPU cloud VM; each crawl fetches 150 pages
> with the 1 s per-host delay. One live run is reported in the next section.

The research report's first recommendation was a Mercator-style frontier (Heydon & Najork, 1999):
priorities in front, one queue per host behind, so that many hosts are in flight while each host
still gets one request at a time. `focused_crawler/host_queues.py` and `async_crawler.py`
implement it; `fcrawl crawl --concurrency N` selects it (the default, 1, is the old crawler).

| Crawler | Pages/s, median [min-max] | Speed-up | Smallest per-host gap | Most requests open to one host |
|---|---|---|---|---|
| Sequential (baseline) | 2.22 [2.21-2.22] | 1.0 | 0.998 s | 1 |
| Async, 5 workers | 9.56 [9.54-9.69] | 4.3 | 1.155 s | 1 |
| Async, 10 workers | 12.67 [12.63-12.72] | 5.7 | 1.154 s | 1 |
| Async, 20 workers | 13.64 [13.49-13.87] | 6.1 | 1.156 s | 1 |
| Async, 40 workers | 13.60 [13.53-13.76] | 6.1 | 1.155 s | 1 |

- The gaps and the open-request counts are measured by the test server, not by the crawler.
- Each async configuration differs from the baseline with p = 0.008 (exact two-sided
  Mann-Whitney test, 5 runs against 5). With 5 runs per group, 0.008 is the smallest value this
  test can give.
- More than 20 workers cannot help with 20 hosts, because a host takes one request at a time.
- The async crawler counts the delay from the end of a response, so its gaps are longer than 1 s.
  The baseline counts from the start of a request; its 0.998 s is the 1 s rule seen from the
  server, 2 ms short because of timing between the two processes.
- Both crawlers fetched 150 pages in every run. An offline test checks that they store the same
  pages, labels and document lengths on a small site (`tests/test_async_crawler.py`).
- Pages are still parsed and indexed on the event loop, one at a time. The research report
  suggested a process pool for this; it is not built.

Reproduce: `python -m focused_crawler.crawl_bench --hosts 20 --pages 150 --latency 0.3
--repeats 5 --workers 5 10 20 40 --out results/project/crawl_bench.json`.

## Crawler: live run on the documentation sites (2026-11-15)

> **Two runs each.** A 200-page crawl of the real Python documentation sites with the default
> profile, run with the async crawler (20 workers) and then with the sequential crawler, twice,
> from the project owner's Windows laptop. The second pair used `--request-log`. The times
> include start-up and depend on the network at that moment; the two pairs differ a lot, so the
> speed-up is not a measured distribution.

| | Async, pair 1 | Sequential, pair 1 | Async, pair 2 | Sequential, pair 2 |
|---|---|---|---|---|
| Wall-clock time | 36.4 s | 242.8 s | 22.7 s | 364.7 s |
| Pages per second | 5.49 | 0.82 | 8.81 | 0.55 |
| Pages fetched (HTML) | 200 (198) | 200 (197) | 200 (197) | 200 (197) |
| Errors / skipped | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Hosts reached | 20 | 20 | 20 | 20 |
| Pages per host (min-max) | 5-13 | 2-21 | 6-13 | 2-21 |
| Pages indexed | 197 | 197 | 196 | 197 |
| Yield | 85.9% | 91.9% | 86.3% | 91.9% |
| OTHER pages | 28 | 16 | 27 | 16 |

Per-host politeness, from the request logs of pair 2 (the crawler's own clock, robots.txt
requests included):

| | Async, 20 workers | Sequential |
|---|---|---|
| Requests | 220 to 20 hosts | 227 to 20 hosts |
| Smallest gap between request starts to one host | 1.024 s | 1.000 s |
| Smallest gap from a response's end to the next start | 1.000 s | 0.000 s |
| Overlapping requests to one host | 0 | 0 |

- Both crawlers kept the 1 s per-host rule on the real sites, and no host had two requests open.
  The sequential crawler counts the delay from the start of a request, so after a response that
  takes more than a second the next request follows at once (the 0.000 s); the async crawler
  rests a full second after each response.
- The async crawl took 6.7 times less time in pair 1 and 16.1 times less in pair 2. The
  sequential crawl alone took 242.8 s and then 364.7 s, so the ratio depends heavily on how slow
  the sites were during that run. The local benchmark above gave 6.1 with 20 workers.
- 142 URLs (pair 1) and 133 URLs (pair 2) were fetched by both crawls of a pair, and every one
  has the same label and document length in both databases.
- The two crawlers chose different pages beyond those, because the async crawler takes the best
  URL of each free host and not the best URL overall. It spread pages more evenly over the hosts
  and fetched more OTHER pages, which is why its yield is lower.

The numbers are in `results/project/live_crawl.json` and the request logs in
`results/project/live_requests_async.json` and `live_requests_seq.json`. Reproduce:
`fcrawl --db data/project/live_async_log.db crawl --max-pages 200 --concurrency 20 --request-log
results/project/live_requests_async.json`, and the same with `--concurrency 1` for the
sequential crawl.

## Not done yet

- The live crawl was run twice per crawler; more repeated runs are needed for a statistical test.
- The async crawler does not read `Crawl-delay` from robots.txt or slow down for slow hosts.
- From the research report: the learned classifier and link scorer, and WARC storage with
  conditional GET, are not started.

- The corpus is not yet filtered to records verified by the M3 ledger.
- The M4 edges do not serve search results yet.
- A crawl with more near-duplicates is needed to measure the effect of collapsing them.
- Loading goes through SQLite one posting per row; it has only been run on this small corpus.
