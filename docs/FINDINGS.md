# Findings (2026-10-26)

A summary of what was built, what the first runs showed, and what is still open.
Raw outputs are in `results/`, and the detailed M4 table is in `docs/m4-results.md`.

## 1. Focused crawler (M1 academic design), `v0.1.0`

**Built:** a crawler scoped to `*.gatech.edu` that respects robots.txt, waits at least 1 s per
host, and enforces page, depth and per-host budgets. A rule-based classifier labels pages
COURSE / FACULTY / LAB / OTHER, and link scoring sends academic paths to the front of the
frontier. Per-type fields are extracted along with configurable CSS selectors. Storage is
SQLite (pages, link graph, Courses/Faculty/Labs tables, positional postings, exact-duplicate
detection). Search uses a TF-IDF baseline through the `fcrawl` CLI.

**Live smoke test:** `fcrawl --db data/smoke.db crawl --max-pages 30`

| Metric | Value |
|---|---|
| Pages fetched | 30 (26 HTML) |
| Useful yield (non-OTHER share) | 88.5% |
| Labels | COURSE 7, FACULTY 9, LAB 7, OTHER 3 |
| Hosts / outlinks / distinct terms | 4 / 6,767 / 1,078 |
| Errors / robots-blocked | 0 / 0 |
| Frontier after 20 pages | 811 queued |

**Observations**
- The priority frontier works: academic pages were fetched before generic ones.
- Rule-based misclassifications are visible. Listing and job pages with "faculty" in the path
  or title came out as FACULTY: `/person/classification/faculty` and
  `/faculty-position-opportunities`. `/research-areas` came out as LAB. This is the recall and
  precision gap that M1 Refinement 1 (the learned classifier) targets.
- The query "computer science faculty" ranked a real faculty profile first, but the home page
  (OTHER) came second. TF-IDF alone does not prefer authoritative pages, which motivates
  M1 Refinement 2 (metadata-aware ranking).
- 30 pages is an end-to-end check, not an evaluation.

**Not built yet:** M1 Refinement 1 (learned classifier), Refinement 2 (metadata-aware
ranking), and the judged-query evaluation (P@5, MRR, coverage).

## 2. Crawl ledger (M2/M3), imported

`../crawl-ledger` was copied into `src/crawl_ledger` (21 tests, seeds and earlier results in
`results/m3/`). The only changes are lint fixes and the seeds path (`config/ledger_seeds.txt`).
For M4 R1, `Header` gained an optional `delta_root`. It enters the header ID only when set, so
M3 header IDs and tests are unchanged.

## 3. Serving layer (M4), `v0.2.0`

**Built:**
- Proof, status and pointer objects with the M4 Table 2 cache rules.
- A stateless gateway with gateway-signed statuses.
- An edge proxy (LRU proof cache, one-round-trip `/latest`).
- A client verifier with IDE and RAG policies.
- R1: revocation deltas committed by `delta_root`, gap fetch, and TTL fallback.
- R2: Bloom-filter summaries with verify-or-fallback sibling fetch.
- aiohttp servers with miss coalescing.
- A trace-driven simulator.

**Simulation** (`python -m edge_serving.sim --compare`): 25 packages x 3 sources, 3 edges,
20,000 Zipf(1.0) reads, 12 epochs of 600 s, 2% of final records revoked per epoch, gossip
delay up to 5 s, modeled latency (5 / 40 / 150 ms).

| Build | T_status | Stale accepts | Stale window | Hit ratio | Gateway req/s | p95 | Gossip B/edge/epoch | Sibling hits |
|---|---|---|---|---|---|---|---|---|
| baseline | 60 s | 13 | 44 s | 0.690 | 2.59 | 305 ms | 0 | 0 |
| baseline | 600 s | 52 | 474 s | 0.805 | 1.62 | 305 ms | 0 | 0 |
| baseline | 3600 s | 481 | 1791 s | 0.835 | 1.38 | 155 ms | 0 | 0 |
| +R1 | none | 2 | 3 s | 0.996 | 0.035 | 5 ms | 988 | 0 |
| +R2 | 60 s | 13 | 44 s | 0.690 | 2.58 | 305 ms | 0 | 41 |
| +R1+R2 | none | 2 | 3 s | 0.996 | 0.029 | 5 ms | 988 | 42 |

**Findings**
1. **The baseline TTL trade-off is confirmed.** A longer status TTL lowers gateway load but
   stretches the window during which a revoked record is still accepted to roughly T_status.
2. **R1 bounds staleness by the gossip delay** (3 s observed, 5 s allowed) and removes status
   and pointer traffic from the gateway, about 75x fewer requests than the 60 s baseline. The
   cost is about 1 KB of header plus delta per edge per epoch.
3. **R2 gives little benefit at this scale.** About 40 proof misses became sibling hits,
   because 25 packages fit in every edge's cache, so most misses are cold misses on new records.
4. **Pointer moves under R1 lag by one header.** Finality reached while sealing epoch *e* is
   committed in the delta of epoch *e + 1*, so R1 edges see a new "latest" record one epoch
   after the gateway does. This follows from committing "what changed that epoch" (M4 3.1).
5. **Miss coalescing works over HTTP.** Five concurrent `/latest/requests/pypi` requests to one
   edge produced 1 upstream fetch and 4 coalesced waiters. The gateway saw exactly 3 requests
   (pointer, proof, status), and the client accepted the record.
6. **The security checks hold in tests.** A tampered proof, a tampered or misattributed
   status, a forged delta entry, a dropped delta entry (delta_root mismatch), a forged sibling
   copy, an implied-INCLUDED claim on a non-final block, and a status older than `max_lag` are
   all rejected, refetched or fall back to the gateway.

**Caveats:** everything is simulated in one process with synthetic content, modeled latency
and an assumed Zipf skew. R1's hit ratio counts statuses and pointers served from applied
deltas as hits. All edges are honest, but a dishonest edge could misreport `synced_epoch`
(acknowledged in M4 3.1).

## 4. Open items

| Item | Next step |
|---|---|
| M1 R1 learned classifier | Label about 200 pages per class from a larger crawl; TF-IDF + logistic regression with a rules fallback |
| M1 R2 metadata ranking | `w1*TF + w2*field_match + w3*recency`, boost only for field-pattern queries |
| M1 evaluation | 30-50 judged queries; P@5, MRR, yield, coverage |
| M4 R2 under cache pressure | `python -m edge_serving.sim --compare --packages 2000 --proof-capacity 500` |
| M4 dishonest edges | Simulate a misreported `synced_epoch` and replayed statuses; measure stale accepts |
| M4 real deployment | Run the aiohttp edges on separate hosts to measure, not model, latency |

## 5. Engineering notes

- Repo history: one feature branch per milestone, merged with `--no-ff`, Conventional Commits,
  tags `v0.1.0` and `v0.2.0`. No remote yet.
- Pre-commit runs ruff and the full test suite, but it leaves untracked files in place. Partial
  commits therefore need `git stash push --keep-index --include-untracked` first, or the hook
  tests the wrong tree.
- The crawl-ledger import landed as one commit (code + lint fixes), because a verbatim-only
  commit would fail the lint hook.
- Test suite: 134 offline tests in 21 modules (12 for M1, 2 for M2/M3 with 21 tests, 7 for M4).
