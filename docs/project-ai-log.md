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

### Rating and critique (Pravesh)

_To be written._
