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
- The table above has only honest edges. See the next section for dishonest ones.

## Dishonest edges (2026-11-07)

Command, for each seed 0-4: `python -m edge_serving.sim --variant r1 --dishonest-edges 1 --attack
withhold --seed 0`. Same setup as above, T_status = 60 s for the baseline, raw rows in
`results/m4/dishonest_edges.json`. Edges cannot forge anything here; they only hide revocations
(`src/edge_serving/dishonest.py`):

- **Baseline replay:** keep serving the last signed status from before the revocation.
- **R1 `freeze`:** stop applying deltas and overstate `synced_epoch`.
- **R1 `withhold`:** apply everything except revocations, and report `synced_epoch` truthfully.

| Build | Attack | Dishonest edges | Stale accepts, mean (range) | Stale window, mean | Share of revoked reads accepted |
|---|---|---|---|---|---|
| baseline | none | 0 of 3 | 7 (0-13) | 24 s | 0.6% |
| baseline | replay | 1 of 3 | 179 (144-268) | 1775 s | 14.2% |
| baseline | replay | 3 of 3 | 519 (421-787) | 1797 s | 41.3% |
| +R1 | none | 0 of 3 | 2 (0-2) | 2 s | 0.1% |
| +R1 | withhold | 1 of 3 | 589 (456-891) | 6533 s | 32.9% |
| +R1 | withhold | 3 of 3 | 1788 (1288-2806) | 6579 s | 100% |
| +R1 | freeze | 1 of 3 | 600 (445-961) | 6579 s | 33.4% |
| +R1 | freeze | 3 of 3 | 1818 (1277-3009) | 6586 s | 100% |

### What this shows
- **R1 is weaker than the baseline against a dishonest edge.** Baseline statuses carry a
  gateway-signed epoch, so `max_lag` caps the replay at about 3 epochs (1800 s). R1 statuses have
  no signed freshness, so the window has no bound: 6600 s is simply the first revocation (600 s)
  to the end of the 7200 s run.
- **Signing `synced_epoch` would not fix this.** `withhold` reports it truthfully and does as well
  as `freeze`. The client cannot tell "no revocation exists" from "the edge left it out", because
  an absent delta entry proves nothing: `INCLUDED` is implied, and an old `PASS` keeps a valid
  branch. A fix needs a proof of non-revocation, or a check that does not go through the edge.
- **Damage scales with the share of reads the edge serves.** One edge of three hides about a third
  of revoked reads, for as long as it keeps lying.
- **What R1 still stops.** A lying edge cannot make a revoked record pass a policy it never met: a
  record with no earlier `PASS` is still rejected by the RAG policy. An edge that freezes and
  admits its lag gets `REFETCH`.

### Caveats on these runs
- Five seeds and one workload. Ranges are wide, probably because revoking a popular record
  dominates a run. Client retries are not modeled: `REFETCH` counts as not accepted.
- The attacker is always active and never detected. No defense is implemented yet.
