# M5: how the experiments were run, and how to run them yourself

This guide explains where the M5 benchmark (`corpus_jobs`) was run, what was done, and how to
repeat it on another machine. The results themselves are in [m5-results.md](m5-results.md).

## What was done

M5 builds three outputs from a crawl snapshot: an inverted index, the 1,000 most frequent terms,
and near-duplicate pairs found with MinHash and locality-sensitive hashing (LSH). The same work is
done by five engine setups, so that they can be compared:

| Engine | What it is |
|---|---|
| `local` | One Python process that keeps everything in memory (the reference output) |
| `hadoop-naive` | The baseline: Hadoop Streaming, one record per token, one job per step |
| `hadoop` | Refinement 1: Hadoop with in-mapper combining, a combiner and a local top-k |
| `spark` | Refinement 2: Spark, tokenize once, cache the tokenized RDD, run every job on it |
| `spark-nocache` | The Spark pipeline without the cache, to isolate the effect of caching |

The work happened in this order:

1. The `corpus_jobs` package was written, with offline tests that check every Hadoop mapper and
   reducer against the single-node output.
2. Three corpora were built: 85 Python-documentation pages from the M3 crawl collection (easy),
   one Common Crawl WET file (average, 20,205 documents) and four WET files (hard, 81,842
   documents). Near-duplicate copies were added to the two Common Crawl corpora so that duplicate
   detection has a known ground truth.
3. The first benchmark run measured the easy and average corpora and was then stopped; the hard
   corpus was estimated with a linear model.
4. The benchmark was later resumed. Naive Hadoop then failed on the hard corpus with
   `java.lang.OutOfMemoryError: Java heap space`. Hadoop's local job runner runs both reducers in
   one Java process, and by default each may buffer 70% of the heap, so together they can exceed
   it. The reducer buffer was lowered to 35% (`mapreduce.reduce.shuffle.input.buffer.percent`,
   the constant `SHUFFLE_BUFFER` in `src/corpus_jobs/hadoop.py`) for every Hadoop run, and the
   average-corpus Hadoop runs were repeated with it.
5. Every engine was measured 3 times at 2 cores on all three corpora, and once at 1 core on the
   easy and average corpora. Every engine's output matched the single node's.

## Where it ran

All measurements were taken on one cloud virtual machine (the cloud workspace that Claude uses),
not on the project owner's laptop:

| Item | Value |
|---|---|
| CPU and memory | 2 virtual CPUs, 7.8 GB (8,022 MiB) |
| Operating system | Ubuntu 24.04.5 LTS, Linux 6.18 |
| Python | 3.13 |
| Java | OpenJDK 21 |
| Hadoop | 3.5.0, local job runner, local file system (no HDFS, no YARN, no cluster) |
| Spark | 4.3.0 (from the PySpark package), local mode |
| Python packages | NumPy 2.5, psutil 7.2 |

The benchmark was not run on the laptop's Linux environment because it has Python 3.10 (the
project needs 3.11 or newer) and about 3.9 GB of memory, while the single node needs about 3.5 GB
on the hard corpus.

## Run it yourself

All commands run from the repository root. Use Linux; on Windows, use WSL2 with Ubuntu. For the
hard corpus, use a machine with at least 8 GB of memory. The easy and average corpora need less.

### 1. Install Python and the project

Python 3.11 or newer is needed (the measurements used 3.13).

```
python3 -m venv .venv-linux
source .venv-linux/bin/activate
pip install -e ".[dev,m5]"
```

The `m5` extra installs NumPy, psutil and PySpark (which includes Spark itself).

### 2. Install Java

```
sudo apt install openjdk-21-jdk-headless
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
```

### 3. Install Hadoop

No configuration is needed: by default Hadoop uses the local job runner and the local file system.

```
mkdir -p ~/tools
curl -O https://dlcdn.apache.org/hadoop/common/hadoop-3.5.0/hadoop-3.5.0.tar.gz
tar xzf hadoop-3.5.0.tar.gz -C ~/tools
export HADOOP_HOME=~/tools/hadoop-3.5.0
export PATH=$HADOOP_HOME/bin:$PATH
```

The SHA-512 of the archive used here is
`04ab94496cc00c8b7a28d03f6308eff8d2a4e7f37a9da5e8e086e4d6fc990e7a94d661908f6a6136039536efb362614b8aecdef185b5fb8ed588f0b152c7aa16`.
Older releases stay available at `https://archive.apache.org/dist/hadoop/common/`.

### 4. Download the Common Crawl files

The corpora use the first four WET files of one segment of the August 2026 crawl
(CC-MAIN-2026-34); each is about 64 MB. The full list of WET files is at
`https://data.commoncrawl.org/crawl-data/CC-MAIN-2026-34/wet.paths.gz`.

```
mkdir -p data/cc
CC=https://data.commoncrawl.org/crawl-data/CC-MAIN-2026-34
P=$CC/segments/1786091384908.68/wet
for i in 0 1 2 3; do
  curl -o data/cc/wet$i.warc.wet.gz \
    $P/CC-MAIN-20260807101845-20260807131845-0000$i.warc.wet.gz
done
sha256sum data/cc/wet*.warc.wet.gz
```

Expected SHA-256 values:

| File | SHA-256 |
|---|---|
| `wet0.warc.wet.gz` | `a7637296d56b737e116998fdff0cb2217e56c2541ffaa95d17960a44efda7af6` |
| `wet1.warc.wet.gz` | `2f5f7b065343d676c345ac54df1cbac574514ca5816ae4f3a461ac723ffeaf66` |
| `wet2.warc.wet.gz` | `9c9eeeeba34465ec05d7ce3a334b21ebce72987ccbabe59d71aa11621c8c45fe` |
| `wet3.warc.wet.gz` | `b96edd5a0971d7e7ecd007469af7013393e1d05fc4afec0b46aff68fbb2c0181` |

### 5. Build the corpora

The easy corpus needs the M3 crawl collection in `data/drift/` (made by
`python -m crawl_ledger.audit_eval collect`). The near-duplicate copies use a fixed seed, so a
rebuild is byte-identical; this was checked for the average and hard corpora.

```
python -m corpus_jobs.corpus drift data/drift --out data/m5/easy.jsonl
python -m corpus_jobs.corpus wet data/cc/wet0.warc.wet.gz \
    --out data/m5/average.jsonl --inject-dups 200
python -m corpus_jobs.corpus wet \
    data/cc/wet0.warc.wet.gz data/cc/wet1.warc.wet.gz \
    data/cc/wet2.warc.wet.gz data/cc/wet3.warc.wet.gz \
    --out data/m5/hard.jsonl --inject-dups 800
sha256sum data/m5/average.jsonl data/m5/hard.jsonl
```

Expected: `average.jsonl` starts `ac488804f690`, and `hard.jsonl` starts `c71f289cb498`. Each
command also writes `<corpus>.truth.json`, the list of injected copies used to score duplicate
detection.

### 6. Check the installation

```
python -m pytest tests/m5
```

With `HADOOP_HOME` set, one test runs refined Hadoop for real and compares it with the single
node; without it, that test is skipped.

### 7. Run one engine

```
python -m corpus_jobs.run spark data/m5/easy.jsonl data/m5/out/try --cores 2
```

The output directory gets the index, the top-k terms, the duplicate pairs (`pairs`) and
`metrics.json` with the wall time and the engine's counters. Engines: `local`, `hadoop-naive`,
`hadoop`, `spark`, `spark-nocache`. Add `--jobs index topk` to skip duplicate detection.

### 8. Run the benchmark

```
python -m corpus_jobs.bench --scenarios easy average hard --repeats 3 \
    --out results/m5/bench.json
python -m corpus_jobs.bench --scenarios easy average --cores 1 --repeats 1 \
    --out results/m5/bench.json
```

The benchmark runs each configuration as a subprocess, samples the memory of the whole process
tree every 0.2 s, checks every output against the single node, scores duplicate detection for the
single node, and writes after every configuration. Running the same command again skips the
configurations already in the output file, so an interrupted run can be resumed.

On the 2-core machine above, the first command took roughly 2 hours (mostly the hard corpus) and
the second about 10 to 15 minutes. Other machines will give other times; compare engines measured
on the same machine.

### Troubleshooting

- **`set HADOOP_HOME to a Hadoop 3.x installation`:** export `HADOOP_HOME` as in step 3.
- **A Hadoop job fails:** the full Hadoop log is written to `<out>/<job>.stderr.log`. If it shows
  `OutOfMemoryError: Java heap space`, lower `SHUFFLE_BUFFER` in `src/corpus_jobs/hadoop.py`.
  Giving the Hadoop client a larger heap (Hadoop 3 reads `HADOOP_HEAPSIZE_MAX`, for example `4g`)
  should also help, but this was not tested.
- **Spark refuses to write output:** the benchmark deletes each run's output directory first; when
  using `corpus_jobs.run` by hand, choose a new output directory or delete the old one.
