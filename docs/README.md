# Documentation

| Document | What it covers |
|---|---|
| [architecture.md](architecture.md) | How the three packages work: components, data model, protocols |
| [usage.md](usage.md) | Every command, its options, the config files and the output formats |
| [development.md](development.md) | Setup, tests, linting, repository layout and the git workflow |
| [FINDINGS.md](FINDINGS.md) | What the live crawls and simulations showed, and the open items |
| [m4-results.md](m4-results.md) | Full M4 serving-layer evaluation table and its setup |
| [ranking-results.md](ranking-results.md) | BM25F ranking vs. the M1 TF ranker on judged queries (draft judgments) |
| [host-rate-results.md](host-rate-results.md) | Ring-wide per-host rate limits with host coordinators (simulation) |
| [m5-results.md](m5-results.md) | M5: index, top-k and near-duplicate jobs on a single node, Hadoop and Spark (few runs, one VM) |
| [m5-reproduce.md](m5-reproduce.md) | M5: what was done, where it ran, and how to run it yourself |
| [crawler-optimization.md](crawler-optimization.md) | M1 crawler: a live 500-page run and a prioritized optimization plan |
| [audit-results.md](audit-results.md) | Canonical block-level audits vs. raw hashing and SimHash on re-fetched pages |
| [plans/](plans/) | Implementation plans per milestone (`00-overview.md`, `01-m4-serving-layer.md`) |
| [design/crawl-ledger-README.md](design/crawl-ledger-README.md) | Original README of the imported M2/M3 prototype (historical) |

Raw outputs (JSON and logs) are in [`../results/`](../results/).
