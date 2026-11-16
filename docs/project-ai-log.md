# Term project: AI-usage log

A running record for the course's AI-usage documentation (OMS CS6675 Important Update for Fall
2026). Each entry gives the prompt as typed, the context the tool had, and what the tool did.
The rating (0 to 5), the critique and the corrections are left for Pravesh to write.

**Tool:** Claude (Anthropic), model Claude Fable 5.2, used through the Claude desktop app with
access to the course folder and the built-in browser. https://claude.ai
**Why this tool:** _to be written by Pravesh (2-3 sentences)._

## 2026-11-15

| # | Prompt (as typed) | Context given | What the tool did |
|---|---|---|---|
| 1 | "can you read the M project document and suggest what to do about term project" | Course folder | Read `M5-Pravesh-Shrestha.pdf`, `docs/FINDINGS.md` and `docs/m5-results.md`. Suggested an end-to-end project joining M1 to M5 and flagged that the PDF's numbers were older than `m5-results.md`. |
| 2 | "this one has all the result M5-Pravesh-Shrestha-updated" | Updated M5 PDF | Read it and ordered the final-project plan stated in its section 8. |
| 3 | "explain in simple term how can I connect what I have worked on for M project into a term project" | Same | Explained the three missing joins (M3 to M5, M5 to M1, M1 to M4). |
| 4 | "here is project description" / "check in the builtin browser" | Canvas page "Project Final Deliverable" | Read the page and mapped the seven report sections to existing work. |
| 5 | "is there a way to connect using hadoop and mapreduce to this project what ideas can be used for this project" | Same | Listed seven ideas; five from the M5 document, two of its own (ranking statistics in MapReduce, PageRank). |
| 6 | "Start working on this project" | Repository, Canvas AI-usage page | Asked three questions (scope, git, this log). Pravesh chose: pipeline + new refinement + ranking + cluster; commit on a feature branch; keep this log. Then wrote `corpus_jobs.corpus crawldb`, `corpus_jobs/load.py`, `tests/m5/test_load.py` and `docs/project-results.md` on branch `feat/project-pipeline`. |
| 7 | "can you research by focusing on this project and what has been done till now for web crawler what other webcrawler are there and how to improve the webcrawler" | A summary of the project written by the tool | Ran Claude's Research mode. The report compares the project with Mercator, IRLbot, UbiCrawler, Apoidea, YaCy, Nutch and others, and recommends (A) an async Mercator-style frontier, (B) a learned classifier and link scorer, (C) WARC storage with conditional GET. The report itself lists sources it could not verify (ODISSEA, MINERVA, TLSNotary, Scrapy and others). |
| 8 | "ok Implement recommendation you found from the research" | The report, the repository | Implemented recommendation A only: `host_queues.py`, `async_crawler.py`, `fcrawl crawl --concurrency`, `crawl_bench.py`, `tests/test_async_crawler.py`. Measured 2.2 pages/s (sequential) against 13.6 pages/s (20 workers) on a local test web. B and C were not implemented. |
| 9 | "Now create a doc use this starter document as an template ... I want to introduce the project for this check in and tell what has been done till now in this document" / "this is for check in 1 only" | JDF starter document, Canvas page "Project Check-In 1" | Wrote `Project-CheckIn1-Pravesh-Shrestha.docx` (introduction, need-finding, baseline design, work completed) and listed the points Pravesh should check, including three citations taken from the research report. |
| 10 | "I need to write a small post in our course group introducing the project and research done for this this should be not more that 300 words" | The check-in draft | Drafted a post of about 230 words. |
| 11 | "can you download those papers here into the project folder" / "use built in browser" | `Papers` folder | Downloaded the Manku (2007) and Zaharia (2012) papers; opened the Broder (1997) paper on IEEE Xplore, which needs a sign-in. |
| 12 | Pravesh reported a live run (async 36.4 s against sequential 242.8 s for 200 pages) and answered "ok" to two proposed next steps | The live-run numbers | Added a request log to the sequential fetcher, `request_gaps`, and `fcrawl crawl --request-log`, so that per-host gaps can be reported on live crawls; updated the check-in draft with the live numbers. |
| 13 | "update this for project checkin" (with the M5 AI usage document attached) | The M5 AI usage document, this conversation | Wrote `AI-Pravesh-Shrestha_Project-CheckIn1.docx` in the same format, with a placeholder for how the live crawls were run. |

## 2026-11-16

| # | Prompt (as typed) | Context given | What the tool did |
|---|---|---|---|
| 14 | "ok now work on next part" | The repository | Chose the next open link itself: the index built from verified ledger records. Wrote `crawl_ledger/snapshot.py`, `crawl_ledger/policy_eval.py`, the `ledger` corpus source, `corpus_jobs.load --from-corpus`, tests, and `docs/project-ledger-index.md`. Ran only offline. |

### Decisions the tool made in entry 14 (for review)

- Which part came next. Pravesh did not name one.
- Two indexing policies, `pass` and `not-failed`, and `pass` as the default.
- The reader re-checks node ID, record ID, signature, chain position and content hash.
- If several accepted records name one URL, the newest is indexed.
- Ledger pages are passed through the M1 tokenizer so that the M1 rankers can search them.

### Decisions the tool made in entry 8 (for review)

- Among ready hosts, the best-priority URL goes first (the report describes Mercator's rule,
  which takes the host whose delay expired first).
- The per-host delay is counted from the end of a response, which is stricter than before.
- The benchmark uses a local test web with a 0.3 s mean latency chosen by the tool.
- Parsing stays on the event loop; the report's process pool was left out.

### Decisions the tool made in entry 6 (for review)

- Rebuilt page text from the stemmed postings, because the crawl database stores no text.
- Left out terms longer than 40 characters, so the job-built index equals the M1 index.
- Kept the lowest page id of each near-duplicate group.
- Put the loader in `corpus_jobs`, so `focused_crawler` still does not depend on other packages.

### Tool errors and corrections so far

- Entry 1: the tool could not find a project handout and said so; the plan was later revised
  against the Canvas page (a front end and a statistical test are required; a cluster is not).
- Entry 6: the first loader run matched only 85,582 of 85,678 postings, because the job tokenizer
  cut terms longer than 40 characters into pieces. The tool found the cause and fixed it.
- Entry 8: the first benchmark run was slow because the test server built each page with a
  quadratic loop, which serialized its responses. The tool found this by profiling and fixed it
  before taking the reported measurements.

### Rating and critique (Pravesh)

_To be written._
