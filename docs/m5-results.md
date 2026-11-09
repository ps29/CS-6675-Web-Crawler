# M5: corpus jobs on a single node, Hadoop MapReduce and Spark (results, 2026-11-10)

> **Few runs, one machine.** Every configuration has 3 runs at 2 cores on all three corpora,
> and 1 run at 1 core on the easy and average corpora. Everything ran on one 2-vCPU, 7.8 GB
> cloud VM with Hadoop's local job runner and no HDFS, so nothing here says how the engines scale
> across machines.

Where it ran, what was done, and step-by-step instructions to repeat the runs are in
[m5-reproduce.md](m5-reproduce.md).

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

**Hadoop setting.** Every Hadoop run on the average and hard corpora uses
`mapreduce.reduce.shuffle.input.buffer.percent=0.35` (Hadoop's default is 0.70). With the
default, naive Hadoop failed on the hard corpus with `java.lang.OutOfMemoryError: Java heap
space` while fetching map output: the local job runner runs both reducers in one JVM, and each
may buffer 70% of the heap. The average-corpus Hadoop runs were repeated with the new setting.
The easy-corpus Hadoop runs predate the change; at 2 MB the buffer never fills, but they were
not repeated.

## Data

| Scenario | Corpus | Documents | Size |
|---|---|---|---|
| easy | 85 Python-docs pages from `data/drift` (the M3 audit collection) | 85 | 2.0 MB |
| average | 1 Common Crawl WET file, CC-MAIN-2026-34, plus 200 injected near-copies | 20,205 | 176 MB |
| hard | 4 WET files, plus 800 injected near-copies | 81,842 | 716 MB |

Injected copies replace 0.5%, 1%, 2% or 5% of a document's words. A copy is a true
near-duplicate when its exact shingle Jaccard is at least 0.8.

## Results at 2 cores, median of 3 runs [min–max]

Average corpus (20,205 pages):

| Engine | Time (s) | Map output records | Shuffle bytes | Peak RSS | Same output |
|---|---|---|---|---|---|
| local | 66.1 [61.9–66.5] | – | – | 1,067 MB | reference |
| hadoop-naive | 173.7 [173.3–180.5] | 83.1 M | 1.97 GB | 1,174 MB | yes |
| hadoop | 109.4 [106.9–112.1] | 22.0 M | 1.10 GB | 1,324 MB | yes |
| spark | 71.6 [66.9–74.0] | – | 0.16 GB* | 1,727 MB | yes |
| spark-nocache | 70.8 [70.2–72.2] | – | 0.16 GB* | 1,628 MB | yes |

Hard corpus (81,842 pages):

| Engine | Time (s) | Map output records | Shuffle bytes | Peak RSS | Same output |
|---|---|---|---|---|---|
| local | 250.1 [247.9–259.4] | – | – | 3,540 MB | reference |
| hadoop-naive | 609.3 [600.9–636.9] | 335.1 M | 7.95 GB | 2,089 MB | yes |
| hadoop | 334.2 [327.4–344.4] | 88.3 M | 4.45 GB | 2,316 MB | yes |
| spark | 258.0 [250.0–290.4] | – | 0.63 GB* | 3,332 MB | yes |
| spark-nocache | 268.7 [262.0–283.9] | – | 0.63 GB* | 2,287 MB | yes |

\* Spark compresses shuffle output by default and Hadoop does not, so these sizes are not
directly comparable. On the hard corpus Spark also spilled about 3.0 GB from memory (0.34 GB
written to disk).

Per-job shuffle on the average corpus, naive → refined:

| Job | Naive | Refined |
|---|---|---|
| index | 754 MB | 345 MB |
| term counts | 475 MB | 84 MB |
| top-k | 72.7 MB | 0.07 MB |
| LSH bands | 672 MB | 672 MB (unchanged) |

On the easy corpus, the fixed cost dominates: local takes 0.8 s, Hadoop 18–20 s and Spark 14 s.

## Results at 1 core (1 run each)

| Engine | Easy | Average |
|---|---|---|
| local | 0.8 s | 60.7 s |
| hadoop-naive | 19.4 s | 247.8 s |
| hadoop | 19.1 s | 150.5 s |
| spark | 12.6 s | 130.0 s |
| spark-nocache | 13.0 s | 140.4 s |

## Near-duplicates

| | Average | Hard |
|---|---|---|
| Pairs reported | 468 | 4,719 |
| Natural pairs (already in the crawl) | 337 | 4,186 |
| Precision (share with exact Jaccard ≥ 0.8) | 93.4% | 94.6% |
| True injected near-duplicates | 135 | 544 |
| Recall | 94.1% | 95.0% |
| Found at 0.5% / 1% / 2% / 5% edits | 100 / 92 / 68 / 9% | 99.6 / 95.7 / 63 / 6.9% |
| Mean error of the similarity estimate | 0.016 | 0.016 |

No LSH bucket exceeded the 200-document cap on any corpus. On the easy corpus, all 4 pairs are
URL variants of the same page (for example `numpy.org/doc/stable/` and `numpy.org/devdocs/`).

## Earlier estimate against the measurement (hard corpus)

Before the hard corpus was run, a linear model from the easy and average corpora
(`results/m5/hard_estimate.json`, made with the old Hadoop setting) predicted:

| Engine | Estimated | Measured |
|---|---|---|
| local | about 266 s, 3.8 GB | 250.1 s, 3.5 GB |
| hadoop-naive | about 707 s | 609.3 s |
| hadoop | about 392 s | 334.2 s |
| spark | about 248 s | 258.0 s |
| spark-nocache | about 246 s | 268.7 s |

## Findings

- **Refinement 1 works.** Aggregating before the shuffle cuts map output records 3.8× and spilled
  records 5.8–6.0×, making Hadoop 37% faster on the average corpus and 45% faster on the hard one.
- **Spark beats both Hadoop designs, but caching helps little.** Spark is 35% faster than refined
  Hadoop on the average corpus and 23% on the hard one. Caching made no difference on the
  average corpus (71.6 s against 70.8 s) and saved 4% on the hard one (258.0 s against 268.7 s),
  at about 1 GB more memory.
- **The single node is still fastest on one 2-core machine.** Its lead over Spark shrinks from 8%
  (average) to 3% (hard). Its memory grows with the corpus (3.5 GB on the hard corpus), while the
  engines' memory grows less. On 1 core, Hadoop and Spark slow down far more than the single node.

## Reproduce

See [m5-reproduce.md](m5-reproduce.md) for the full steps (installing Java and Hadoop, downloading
the Common Crawl files, building the corpora with checksums, and running the benchmark).

Raw results are in `results/m5/bench.json`, with provenance. The benchmark resumes: configurations
already in the output file are skipped.
