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

## Proofs of non-revocation (R1-S) and client spot-checks (2026-11-07)

Command: `python -m edge_serving.sim --variant r1s --dishonest-edges N --attack A --seed S` for
seeds 0-4, same setup as above; spot-check rows add `--spot-check P` (2 strikes). Raw rows are in
`results/m4/state_proofs.json`. Baseline and plain-R1 rows without spot-checks are repeated from
`results/m4/dishonest_edges.json`; plain-R1 `replay` and the spot-check rows are new.

**R1-S** (`src/edge_serving/state_proofs.py`): each header also commits a `state_root`, a Merkle
root over every final record's current state. Edges rebuild that state from deltas (which then
also list newly final records) and serve a status as a branch to the root. The client checks the
branch against its own header chain and refetches when the *proven* epoch is more than `max_lag`
behind its head. A lying edge can therefore no longer leave a revocation out: it can only replay a
state that was true at an earlier epoch.

| Build | Attack | Dishonest edges | Stale accepts, mean (range) | Stale window, mean | Share of revoked reads accepted | Reads accepted, mean |
|---|---|---|---|---|---|---|
| baseline | replay | 1 of 3 | 179 (144-268) | 1775 s | 14.1% | 18,923 |
| +R1 | withhold | 1 of 3 | 589 (456-891) | 6533 s | 33.1% | 18,801 |
| +R1 | replay | 1 of 3 | 589 (456-891) | 6533 s | 33.1% | 18,801 |
| R1-S | none | 0 of 3 | 2 (0-2) | 2 s | 0.1% | 18,214 |
| R1-S | withhold | 1 of 3 | 2 (0-2) | 2 s | 0.1% | 12,675 |
| R1-S | withhold | 3 of 3 | 0 (0-1) | 0 s | 0.0% | 1,671 |
| R1-S | freeze | 1 of 3 | 70 (47-95) | 1760 s | 4.0% | 14,330 |
| R1-S | freeze | 3 of 3 | 205 (122-291) | 1789 s | 11.4% | 6,659 |
| R1-S | replay | 1 of 3 | 243 (181-373) | 1795 s | 13.8% | 18,456 |
| R1-S | replay | 3 of 3 | 722 (555-1168) | 1798 s | 40.5% | 18,934 |

**Spot-checks.** With probability P, the client re-checks an accepted status with the gateway; a
mismatch rejects the read, and after 2 mismatches the client stops using that edge.

| Build | Attack, 1 of 3 edges | P | Stale accepts, mean (range) | Stale window, mean | Lying edge blocked | Honest edges blocked (total, 5 runs) | First block, mean | Gateway req/s |
|---|---|---|---|---|---|---|---|---|
| +R1 | withhold | 0.01 | 193 (110-247) | 3394 s | 5 of 5 runs | 0 | 4065 s | 0.060 |
| +R1 | withhold | 0.05 | 56 (18-98) | 1545 s | 5 of 5 runs | 0 | 2174 s | 0.161 |
| R1-S | replay | 0.05 | 63 (18-132) | 1503 s | 5 of 5 runs | 0 | 2350 s | 0.160 |
| baseline (T_status 60 s) | replay | 0.05 | 28 (11-60) | 927 s | 5 of 5 runs | 3 | 1613 s | 2.901 |

### What this shows
- **R1-S bounds a lying edge the way the baseline does.** Every R1-S attack stays under about
  1800 s, which is `max_lag` (3 epochs) of 600 s, against about 6500 s (the end of the run) for
  plain R1. The replay rows match the baseline's replay rows closely (13.8% vs 14.1% of revoked
  reads with one bad edge), but R1-S needs no gateway key and keeps R1's low gateway load.
- **Withholding now fails safe.** An edge that leaves a revocation out of its state computes a
  tree that disagrees with the header, so every status it serves is rejected. No stale record is
  accepted, but that edge's reads all fail (accepted reads fall from 18,214 to 12,675 with one
  such edge). Clients should treat repeated branch failures as a reason to switch edges.
- **Freezing costs availability more than replaying.** A frozen edge's branches age out together
  after `max_lag`, so its reads become REFETCH; replay keeps fresh branches for everything except
  the revoked records, so it hides revocations at almost no visible cost. Replay is therefore the
  attack that matters, and `max_lag` is its bound.
- **R1-S is nearly free when edges are honest.** Same stale accepts, gateway load and p95 latency
  as R1; gossip grows from about 989 to 1118 bytes per edge per epoch (+13%) because deltas now
  list newly final records and headers carry a `state_root`. Edges must also hold the state of
  every final record and rebuild one Merkle tree per epoch.
- **Spot-checks find a lying edge, at a gateway cost.** At P = 0.05 the lying edge was blocked in
  every run, after about 36 minutes on average, and stale accepts fell to about a tenth of plain
  R1's. Gateway load rose about 4.7x over R1 but stayed about 16x below the 60 s baseline.
- **Spot-checks misfire on TTL caches.** With the baseline, honest edges legitimately serve a
  status up to T_status old, so a spot-check can disagree with an honest edge. Three honest edges
  were blocked across five runs. Under R1 and R1-S, honest edges lag only by the gossip delay and
  none were blocked. A deployment should accept a mismatch when the edge's state is still within
  its stated freshness, or require more strikes.

### Caveats on these runs
- Same five seeds and workload as the earlier dishonest-edge runs; one bad edge always serves
  one third of reads. A smarter attacker could replay only the revoked records it cares about.
- `max_lag` bounds R1-S only if the client's own header chain is current. The client here reads
  headers straight from the ledger; in a deployment it must get them from more than one source,
  or a dishonest edge could also serve it an old chain.
- Client retries are not modeled: REFETCH counts as not accepted.
