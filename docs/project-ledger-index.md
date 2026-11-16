# Term project: building the index from verified ledger records (2026-11-16)

> **Offline content only.** Everything here was run with the ledger's offline fetcher, whose
> pages are one line of test text each and never change. It shows that the link works and what
> each policy lets through. It does not show search quality, and no live ledger crawl has been
> indexed yet.

## What was connected

The crawl ledger (M3) now feeds the index job (M5), which already feeds the search engine (M1):

1. `python -m crawl_ledger.snapshot --limit 25 --audit-rate 0.5 --out data/project/ledger`
   crawls with simulated peers, audits a sample of the records, and writes a snapshot: the
   peers' public keys, every record with its audit verdict, and the content each record hashed.
   Add `--offline` to run without the network.
2. `python -m corpus_jobs.corpus ledger data/project/ledger --ledger-policy pass --min-tokens 1
   --out data/project/ledger.jsonl` checks every record again and writes the job's input from
   the accepted pages only. It also writes `ledger.jsonl.records.json`, which names the record
   behind each page.
3. `python -m corpus_jobs.run local data/project/ledger.jsonl data/project/out/ledger`
   builds the index (any engine).
4. `python -m corpus_jobs.load --from-corpus --corpus data/project/ledger.jsonl
   --job-out data/project/out/ledger --out-db data/project/ledger_search.db` writes the search
   database.
5. `fcrawl --db data/project/ledger_search.db search "json"` shows, under each result, the ID
   of the record that vouches for it, the peer that signed it, and the audit verdict.

## What the reader checks

Step 2 trusts nothing in the snapshot files. For each record it recomputes the peer's node ID
from its public key and address, the record ID, the Ed25519 signature, the record's place in
its peer's hash chain, and the hash of the stored content. The tests change each of these in
turn and confirm that the record is rejected (`tests/ledger/test_snapshot.py`).

Two policies then decide on the audit verdict:

- `pass`: only records whose audit returned PASS. A record that was not audited is left out.
- `not-failed`: every record except those whose audit returned FAIL.

## Coverage against forged pages

20 offline crawls per audit rate (seeds 1 to 20), 25 records each, 20 peers of which 4 forge
half of their records. On average 2.95 of the 25 records were forged.

| Audit rate | Policy | Honest records indexed | Forged records indexed (mean) | Runs with a forged page indexed |
|---|---|---|---|---|
| 0.2 | pass | 20.3% | 0.00 | 0 of 20 |
| 0.2 | not-failed | 100% | 2.40 | 18 of 20 |
| 0.5 | pass | 51.2% | 0.00 | 0 of 20 |
| 0.5 | not-failed | 100% | 1.35 | 12 of 20 |
| 0.8 | pass | 79.5% | 0.00 | 0 of 20 |
| 0.8 | not-failed | 100% | 0.50 | 6 of 20 |
| 1.0 | pass | 100% | 0.00 | 0 of 20 |
| 1.0 | not-failed | 100% | 0.00 | 0 of 20 |

- The `pass` policy never indexed a forged page, and it indexes only as many honest pages as
  were audited.
- The `not-failed` policy indexes every honest page, and it lets through the forged pages that
  were not audited.
- This is the trade-off one would expect from the definitions; the sweep confirms that the code
  behaves that way. Raw numbers: `results/project/ledger_policy.json`. Reproduce:
  `python -m crawl_ledger.policy_eval --seeds 20 --out results/project/ledger_policy.json`.

## Limits

- Offline pages never change, so no honest record is INCONCLUSIVE here. On live pages some are,
  and the `pass` policy leaves those out too, so its live coverage will be lower than above.
- A forging peer in the simulation appends one comment to the page. The audit compares hashes,
  so any change is caught the same way, but only this one kind was run.
- The audit verdicts in a snapshot are not signed. A reader has to trust the auditor that wrote
  them. The M3 refinements settle verdicts on the header chain; that is not exported yet.
- Pages built from a ledger snapshot have no title, label or extracted fields, because the M1
  classifier and extractors are not run on them. BM25F then has only the body to work with.
- The M4 edges still serve records and not search results.
