# Plan 01: M4 serving layer over the M2/M3 crawl ledger

## Context
M4 (`M4-Pravesh-Shrestha.docx`) designs how verified crawl records from the M2/M3
Python-documentation crawler (`../crawl-ledger`) are served at scale to IDE plugins,
retrieval pipelines and indexers. The final project is M2 + M3 + M4 as one system. The
academic focused crawler (M1, `src/focused_crawler`, tagged `v0.1.0`) stays in the repo as-is.

## Layout
```
src/focused_crawler/   M1 (unchanged)
src/crawl_ledger/      M2/M3 imported from ../crawl-ledger (record, merkle, blocks, ring, peer,
                       audit, r2, verify, fetcher, sim, sim_m3) + its tests under tests/ledger/
src/edge_serving/      M4
  objects.py     Pointer / Proof / Status objects and Cache-Control rules (M4 Table 2)
  ledger_view.py Read API over the M3 ledger: lookup, block_of, is_final, verdict, rep, head_epoch
  gateway.py     Stateless verification gateway: GATEWAY_GET for /proof, /status, /latest
  edge.py        Edge proxy: LRU proof cache, pointer/status caches, Cache-Control freshness,
                 EDGE_GET and one-round-trip EDGE_GET_LATEST, miss coalescing
  client.py      CLIENT_CHECK (VERIFY_RECORD + status signature + max_lag policy)
  revocation.py  R1: per-epoch revocation delta, delta_root committed in the header,
                 ON_HEADER (gap fetch, TTL fallback), CHECK_STATUS_R1 via Merkle branch
  summary.py     R2: Bloom-filter summaries, sibling lookup PROOF_MISS with verify-or-fallback
  http_app.py    asyncio HTTP front ends (aiohttp) for gateway and edge: /latest, /proof, /status
  sim.py         Trace-driven evaluation (simulated clock, no network)
```

## Milestones (feature branches, `--no-ff` merges)
1. **chore/import-crawl-ledger**: copy the M2/M3 code and tests in verbatim (one commit), then a
   separate commit for ruff/format fixes and packaging so the import diff stays reviewable.
2. **feat/m4-objects-gateway**: objects, ledger view, and a gateway with finality-gated
   `immutable` proofs and signed statuses.
3. **feat/m4-edge-client**: an edge cache honoring `max-age`/`s-maxage`/`no-store`/`immutable`,
   miss coalescing, and the client verifier with consumer policies (IDE: INCLUDED; RAG: PASS).
4. **feat/m4-r1-revocation**: adds `delta_root` to `Header` (included in the header ID), delta
   building in the block builder, edge `ON_HEADER`, `synced_epoch` stamping, and client branch checks.
5. **feat/m4-r2-summaries**: a Bloom filter (size/k from target FP rate), periodic summary
   exchange, and sibling fetch that falls back to the gateway when a copy fails VERIFY_RECORD.
6. **feat/m4-http**: aiohttp gateway and edge servers plus a demo script running 1 gateway and
   3 edges locally.
7. **feat/m4-evaluation**: a Zipf trace over `seeds.txt` packages with injected audit failures.
   It compares baseline (several T_status values), +R1, +R2, and both, measuring stale-serve
   window, stale responses, gateway req/s, hit/byte-hit ratio, gossip bytes/epoch,
   inter-edge bytes and Bloom FP rate. Results go to `results/` and `docs/`.

## Status (2026-10-26)
Milestones 1-7 are done. Evaluation ran before HTTP because it produces the M4 comparison.
Deviation: the crawl-ledger import landed as one commit (verbatim code + lint fixes), since a
verbatim-only commit would fail the ruff hook. Open follow-ups: an evaluation under cache pressure
for R2 (`--packages 2000 --proof-capacity 500`), and dishonest-edge (misreported synced_epoch)
experiments.

## Testing
Offline pytest for every component. Security tests cover a tampered proof, a tampered status
entry, a dropped delta entry (delta_root mismatch), a replayed old status (max_lag), and a
sibling serving a forged proof. All must be rejected or fall back.

## Verification
`pytest` passes; `python -m edge_serving.sim --compare` produces the comparison table;
the demo script serves `/latest/requests/pypi` through an edge, and the client accepts it.
