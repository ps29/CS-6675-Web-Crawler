# crawl-ledger

Prototype of the M3 design "Verifiable Crowdsourced Crawling": a provenance and
integrity layer on top of the M2 decentralized Python-documentation crawler.
Milestone 1 implements the **baseline** (M3 Section 2) as a simulated ring of peers
in one process. R1 (Merkle block ledger) and R2 (deterministic audits + reputation)
are not built yet.

## What is implemented

| Module | M3 component |
|---|---|
| `crawl_ledger/fetcher.py` | (1) Crawler: source router, polite downloader (robots.txt, 1 s per host), link extractor |
| `crawl_ledger/record.py`, `peer.py` | (2) Record builder + signer (Ed25519), (3) hash-chained log; node ID = hash(public key + IP) |
| `crawl_ledger/audit.py` | (5) Auditor: verify, re-fetch, compare; INCONCLUSIVE when the source signals a change |
| `crawl_ledger/verify.py` | Verification API: record ID, signature, chain position |
| `crawl_ledger/ring.py` | Chord-style ownership of packages (successor of hash(name)); no finger tables |
| `crawl_ledger/sim.py` | Runs peers (some dishonest), crawls real sources, audits, reports metrics |

Dishonest peers report the hash of altered content for a share of their records
(`--forge-rate`); the forged flag is kept only as ground truth for measuring detection.

## Run

```
pip install -r requirements.txt
python -m unittest discover -s tests -t .           # 7 offline tests
python -m crawl_ledger.sim --offline                # no network
python -m crawl_ledger.sim --peers 20 --dishonest 0.5 --forge-rate 0.5 --out results/run.json
```

Options: `--limit` packages from `seeds.txt`, `--audit-rate`, `--wait` seconds between
crawl and audit, `--delay` per-host politeness. GitHub READMEs are crawled only if
`GITHUB_TOKEN` is set (unauthenticated limit is 60 requests/hour).

## First results (12 Oct 2026, 25 seed packages, 20 peers)

| Run | Records | Forged | Detection | Honest false rejections | Mean record | Verify |
|---|---|---|---|---|---|---|
| `live_run1` (20% dishonest, 30% forge) | 27 | 0 | n/a | 0 / 27 | 538 B | 0.13 ms |
| `live_run2` (50% dishonest, 50% forge) | 27 | 8 | 8 / 8 | 0 / 19 | 537 B | 0.11 ms |

What these do **not** show yet:
- Audits ran about 25 s after the crawl, so pages had almost no time to change. The
  false-rejection rate over hours or days is the open question (next milestone:
  re-fetch the same pages 24 h later).
- 27 records is far too small for rates; it is a working end-to-end check, not an evaluation.
- In run 1 no package fell in a dishonest peer's key range, which is itself a lesson:
  with ring ownership, forgery exposure depends on how many keys dishonest peers own.
- The forger alters content in a fixed way; a smarter forger (forging only pages that
  change often) is not simulated.
- Records are about 537 bytes as JSON with hex encoding, not the ~300 bytes assumed in
  the M3 storage estimate (binary encoding would be smaller).
- Only 2 of 20 PyPI packages linked to a `*.readthedocs.io` site; many projects host
  docs on their own domains, which the extractor does not follow yet.
