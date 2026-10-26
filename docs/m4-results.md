# M4 evaluation: first results (2026-10-26)

Command: `python -m edge_serving.sim --compare --out results/m4/compare_default.json`

Setup (defaults): 25 seed packages x 3 sources, 16 peers, 3 edges, 20,000 Zipf(1.0) reads over
12 epochs of 600 s (2 h simulated), 5% of packages re-crawled per epoch, 2% of final records
revoked and 10% passed per epoch, header gossip delay up to 5 s, IDE policy (accept INCLUDED or
PASS, max_lag 3). Latency is modeled: 5 ms edge, 40 ms sibling, 150 ms per gateway fetch.

| Build | T_status | Stale accepts | Stale window | Hit ratio | Gateway req/s | p95 | Gossip B/edge/epoch | Sibling hits |
|---|---|---|---|---|---|---|---|---|
| baseline | 60 s | 13 | 44 s | 0.690 | 2.59 | 305 ms | 0 | 0 |
| baseline | 600 s | 52 | 474 s | 0.805 | 1.62 | 305 ms | 0 | 0 |
| baseline | 3600 s | 481 | 1791 s | 0.835 | 1.38 | 155 ms | 0 | 0 |
| +R1 | none | 2 | 3 s | 0.996 | 0.035 | 5 ms | 988 | 0 |
| +R2 | 60 s | 13 | 44 s | 0.690 | 2.58 | 305 ms | 0 | 41 |
| +R1+R2 | none | 2 | 3 s | 0.996 | 0.029 | 5 ms | 988 | 42 |

"Stale accept" means the client accepted a record whose revoking header had already been sealed.
The stale window is the longest such gap.

## What this shows
- The baseline TTL trade-off is real. A longer T_status lowers gateway load but stretches the
  revoked-record window to roughly T_status (1791 s at T_status = 3600 s).
- R1 bounds the window by the gossip delay (3 s at most here vs. up to 5 s allowed) and removes
  status/pointer traffic from the gateway, which then serves only proof misses (~75x fewer
  requests than the 60 s baseline). The cost is about 1 KB of header + delta per edge per epoch.
- R2 turns about 40 proof misses into sibling hits. This is a small gain at this scale, because
  25 packages fit easily in every edge's cache, so most misses are cold misses on new records.

## Caveats
- Everything is simulated in one process: synthetic content, a modeled latency, and an assumed
  Zipf skew. These are working end-to-end checks, not measurements of a deployment.
- R1's hit ratio counts statuses and pointers served from applied deltas as hits.
- With only 25 packages, R2 has little to share. Re-run with `--packages 2000 --proof-capacity 500`
  to see sibling sharing under cache pressure.
- R1 edges trust their own `synced_epoch`. A dishonest edge could misstate it (M4 Section 3.1);
  the simulation has only honest edges.
