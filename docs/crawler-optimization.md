# Focused crawler (M1): live run and optimization strategy (2026-11-10)

This note records a live run of the M1 focused crawler (`focused_crawler`) and a code review of
where its time goes, with a prioritized list of improvements. The review was read-only: no code
was changed.

## Live run

One crawl with the default Python-documentation profile (`config/default.yaml`), run from the same
2-vCPU cloud virtual machine as the M5 benchmark:

```
python -m focused_crawler.cli --db <new database> crawl --max-pages 500
python -m focused_crawler.cli --db <new database> stats
```

| Measure | Value |
|---|---|
| Pages fetched | 500 (494 HTML), 0 errors, 0 skipped |
| Wall-clock time | 356 s, about 1.4 pages per second |
| Peak memory | 288 MB |
| Pages stored / indexed / exact duplicates | 497 / 493 / 1 |
| Sites reached | 20 of 21 scopes |
| Labels | REFERENCE 239, TUTORIAL 83, CHANGELOG 83, HOWTO 70, OTHER 19 |
| Links / distinct terms | 76,261 / 49,203 |
| URLs still queued at the end | about 3,900 |
| Pages by depth | 0: 21, 1: 265, 2: 141, 3: 8, 4: 40, 5: 22 |

Pages were spread evenly over the sites (37 to 40 each from pandas, docs.python.org, SciPy,
Matplotlib and NumPy; 6 to 8 from attrs, Click, Requests and Jinja), which is the effect of
`host_penalty: 0.02` in the default config.

Caveats: this is one run; an offline test run may have used the same CPU during part of the crawl; and
nothing in the crawler records how its time splits between network, politeness sleep and CPU.

**What the searches showed.** "pytest fixture" and "pandas merge dataframe" returned the expected
documentation pages. "json.dumps" and "asyncio event loop" did not, because the crawl never
reached `docs.python.org/3/library/json.html` or any asyncio page: with 500 pages over 20 sites,
most pages are one or two links from the seeds. These searches used the default `tf` ranker.

## Where the time goes

**The crawl is limited by sequential fetching, not by politeness, CPU or the database.**

- `Crawler.crawl` (`crawler.py`) handles one URL at a time, so only one HTTP request is ever in
  flight.
- `Fetcher._polite_wait` (`fetcher.py`) sleeps until 1 s has passed since the last request to the
  same host. When the next URL in the frontier is on a host that was just visited, the crawler
  sleeps instead of fetching from another host.
- With 20 sites, the 1 s per-host rule would allow about 20 requests per second; the run reached
  1.4 pages per second.
- Offline measurements of the processing pipeline on fixture pages (median of 60 runs): parsing,
  classifying, extracting, indexing and storing take a few milliseconds for a 2.6 KB page and about
  0.13 s for a synthetic 95 KB page; SQLite commits, made every 20 pages, cost about 0.4 ms per
  page. These are secondary until fetching is made concurrent.

## Problems found in the code

1. **Wasted fetches on redirects.** If a redirect lands on a URL that is already queued but not yet
   crawled, `Crawler._process` discards the fetched page, and the same URL is fetched again later.
2. **Redirect hops are not checked.** `Fetcher.fetch` follows redirects inside
   `session.get(..., allow_redirects=True)`, so later hops are not checked against robots.txt or
   delayed; out-of-scope targets are rejected only after the fetch.
3. **robots.txt handling.** `Crawl-delay` is not read. A robots.txt that returns a server error is
   parsed as empty (allow everything), and a network error also allows everything; RFC 9309 says
   a server error should be treated as "disallow everything".
4. **Retries.** 429 and 5xx responses are retried after only the 1 s politeness delay, and
   `Retry-After` is ignored.
5. **No download size cap.** Bodies are downloaded in full before the content type is checked.
6. **Duplicates are still processed.** Pages marked as exact duplicates still get postings and
   their links are still followed. `Storage.document_frequency` counts duplicates while
   `Storage.postings` excludes them, so the two disagree.
7. **Canonicalization.** The `canonical` link is extracted as a field but not used to deduplicate;
   `/` and `/index.html` count as different URLs.
8. **No conditional GETs and no resume.** `If-Modified-Since` and ETags are not used on re-crawls,
   and the frontier lives only in memory, so a stopped crawl cannot resume.

## Prioritized improvements

Every item keeps robots.txt, the 1 s per-host minimum and the identifying User-Agent.

| # | Change | Effort | How to measure |
|---|---|---|---|
| 1 | Time each step (fetch, sleep, parse, classify, store) and write it to `results/m1/` | Small | Time split per page |
| 2 | Keep the page when a redirect lands on a queued URL | Small | Duplicate fetches per 500 pages |
| 3 | When the next host is not ready, pop a different host instead of sleeping | Small–medium | Sleep time and pages per minute |
| 4 | Fetch several hosts in parallel, one request in flight per host, one writer for SQLite (`crawl_ledger/hostrate.py` has a reusable per-host limiter) | Medium–large | Pages per minute; maximum observed requests per second per host must stay at or below 1 |
| 5 | Follow redirects manually and check robots.txt on each hop; honor `Crawl-delay`; treat robots.txt 5xx as disallow | Small–medium | Offline tests with fake routes |
| 6 | Exponential backoff and `Retry-After` | Small | 429/503 responses per crawl |
| 7 | Stream downloads with a size cap | Small | Bytes downloaded for non-HTML URLs |
| 8 | Skip indexing and link-following for duplicates; make document frequency exclude them | Small | Postings rows; ranking metrics must not get worse |
| 9 | Cheaper link-context extraction and API-block detection in the parser | Medium | Offline stage timings |
| 10 | Persist the frontier; store ETags and send conditional GETs | Medium | Share of 304 responses on a re-crawl |

Item 4 is the largest gain and a good final-project study (throughput against politeness). Items
3 and 10 also fit a project: scheduling policy, and incremental re-crawling. The rest is
engineering cleanup.
