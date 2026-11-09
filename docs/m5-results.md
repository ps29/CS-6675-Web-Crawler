# M5: corpus jobs on a single node, Hadoop MapReduce and Spark (results, 2026-11-09)

> **Preliminary: only a few runs.** The easy and average corpora each have 3 runs per
> configuration at 2 cores, and 1 run at 1 core for the 85-page corpus. The hard corpus (81,842
> pages) was **not run**: its numbers are linear-model estimates (`corpus_jobs.estimate`), not
> measurements. Everything ran on one 2-vCPU, 7 GB cloud VM, with Hadoop's local job runner and
> no HDFS, so nothing here says how the engines scale across machines.

`corpus_jobs` builds three outputs from a crawl snapshot (JSON lines `doc, url, text`) with five
engine setups. The tokenizer, MinHash and output format are shared, so every engine produces the
same files.

The three outputs:

- an inverted index, one line per term (`term, df, doc:tf, ...`);
- the top 1,000 terms;
- MinHash/LSH near-duplicate pairs: 5-word shingles, 128 hashes, 16 bands × 8 rows, estimated Jaccard ≥ 0.8.

The five engine setups:

| Engine | Design |
|---|---|
| `local` | One Python process, everything in memory |
| `hadoop-naive` | Baseline: Hadoop 3.5.0 Streaming, one record per token, no combiner, one job per step |
| `hadoop` | Refinement 1: in-mapper combining, combiner, local top-k |
| `spark` | Refinement 2: Spark 4.3.0, tokenize once, cached RDD, all jobs on it |
| `spark-nocache` | Spark without the cache, to isolate caching |

## Data

| Scenario | Corpus | Documents | Size |
|---|---|---|---|
| easy | 85 Python-docs pages from `data/drift` (the M3 audit collection) | 85 | 2.0 MB |
| average | 1 Common Crawl WET file, CC-MAIN-2026-34, plus 200 injected near-copies | 20,205 | 176 MB |
| hard | 4 WET files, plus 800 injected near-copies | 81,842 | 716 MB |

Injected copies replace 0.5%, 1%, 2% or 5% of a document's words. A copy is a true near-duplicate when its exact shingle Jaccard is at least 0.8.

## Results: average corpus, 2 cores, median of 3 runs [min–max]

| Engine | Time (s) | Map output records | Shuffle bytes | Peak RSS | Same output |
|---|---|---|---|---|---|
| local | 66.1 [61.9–66.5] | – | – | 1,067 MB | reference |
| hadoop-naive | 189.0 [185.6–206.7] | 83.1 M | 1.97 GB | 1,319 MB | yes |
| hadoop | 110.1 [109.4–115.8] | 22.0 M | 1.10 GB | 1,083 MB | yes |
| spark | 71.6 [66.9–74.0] | – | 0.16 GB* | 1,727 MB | yes |
| spark-nocache | 70.8 [70.2–72.2] | – | 0.16 GB* | 1,628 MB | yes |

\* Spark compresses shuffle output by default and Hadoop does not, so these sizes are not directly comparable.

Per-job shuffle, naive → refined:

| Job | Naive | Refined |
|---|---|---|
| index | 754 MB | 345 MB |
| term counts | 475 MB | 84 MB |
| top-k | 72.7 MB | 0.07 MB |
| LSH bands | 672 MB | 672 MB (unchanged) |

On the easy corpus, the fixed cost dominates: local takes 0.8 s, Hadoop 18–20 s and Spark 14 s.

Near-duplicates on the average corpus:

- **Pairs reported:** 468.
- **Precision:** 93.4%, measured as the share of pairs whose exact Jaccard is at least 0.8.
- **Recall:** 94.1% of the 135 true injected near-duplicates. By edit rate: 0.5% → 100%, 1% → 92%, 2% → 68%, 5% → 9%.
- **Natural pairs:** 337 pairs that were already in the crawl.
- **Easy corpus:** 4 pairs, all URL variants of the same page (for example `numpy.org/doc/stable/` and `numpy.org/devdocs/`).

## Estimate for the hard corpus (not measured)

These are linear-model estimates from the easy and average corpora, at 2 cores:

| Engine | Estimate |
|---|---|
| local | about 266 s, about 3.8 GB peak memory |
| hadoop-naive | about 707 s |
| hadoop | about 392 s |
| spark | about 248 s |
| spark-nocache | about 246 s |

Raw data is in `results/m5/hard_estimate.json`.

## Findings

- **Refinement 1 works.** Aggregating before the shuffle cuts map output records 3.8× and spilled records 5.8×, and makes Hadoop 42% faster.
- **Spark is faster than both Hadoop designs, but caching did not help.** Spark is 35% faster than refined Hadoop. Caching made no difference for these one-pass jobs: it saved time in top-k and cost time while it was filled during the index stage.
- **The single node is still fastest on one 2-core machine.** The engines' fixed cost decides this. Memory, about 3.8 GB estimated for the hard corpus, is the single node's first limit.

## Reproduce

```
python -m corpus_jobs.corpus wet data/cc/wet0.warc.wet.gz --out data/m5/average.jsonl --inject-dups 200
HADOOP_HOME=... python -m corpus_jobs.bench --scenarios easy average --repeats 3 --out results/m5/bench.json
python -m corpus_jobs.estimate --out results/m5/hard_estimate.json
```

Raw results are in `results/m5/bench.json`, with provenance.
