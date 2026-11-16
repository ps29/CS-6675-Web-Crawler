# Term project: the LSH shuffle refinement, collapsing near-duplicates, and a Hadoop cluster (2026-11-16)

> **One machine.** Everything here ran on one cloud VM with 2 vCPUs and 7.8 GB of memory. It is
> faster than the VM of the M5 runs (refined Hadoop took 210.6 s on the hard corpus here against
> 334.2 s in M5), so compare numbers only within one table.
>
> **Counter correction.** Until 2026-11-16, `hadoop.parse_counters` added up every report of a
> counter in the log. The local job runner prints each counter twice, so every local-mode
> counter (map output records, shuffle bytes, spilled records, the custom counters) was exactly
> twice its real value; this was checked for every counter of every job. The numbers below are
> the real ones, and so are those of the M5 runs where this page quotes them. Times and ratios
> were not affected.

## 1. Refinement: a smaller shuffle for near-duplicate detection (`hadoop-lsh`)

M5 found that the band job of near-duplicate detection was the largest part of refined Hadoop's
shuffle: every document's whole MinHash signature (128 numbers) travels through the shuffle once
for each of the 16 bands. `hadoop-lsh` is refined Hadoop with a different near-duplicate stage:

1. `sigs` (map-only, nothing shuffled): one line per document with its signature.
2. `bands`: the mapper reads `sigs` and emits only `(band key, document)`. Each reducer first
   collects its buckets that hold two or more documents (most hold one), then reads from the
   `sigs` output only the signatures of documents in those buckets, and compares them as before.
3. `pairs`: unchanged.

The output is byte-for-byte the same as the single node's on both corpora (checked by the
benchmark) and in the offline and real-Hadoop tests. Reproduce:
`python -m corpus_jobs.bench --scenarios average hard --engines local --repeats 1 --out results/project/lsh_bench.json`,
then the same with `--engines hadoop hadoop-lsh --repeats 3`.

Medians of 3 runs [min-max], 2 cores, local job runner:

| Corpus | Design | Time (s) | Band-job shuffle | Total shuffle | Peak memory |
|---|---|---|---|---|---|
| average (20,205 pages) | refined Hadoop | 71.5 [69.5-71.5] | 336 MB | 551 MB | 1,155 MB |
| average | hadoop-lsh | 70.5 [69.3-70.7] | 7.3 MB | 222 MB | 1,113 MB |
| hard (81,842 pages) | refined Hadoop | 210.6 [208.4-212.2] | 1.36 GB | 2.22 GB | 2,454 MB |
| hard | hadoop-lsh | 199.1 [198.7-200.8] | 29.4 MB | 0.89 GB | 1,814 MB |

The single node took 43.9 s and 175.1 s (one run each) on this VM.

- The band job's shuffle fell by 97.8% on both corpora, and the whole pipeline's shuffle by 60%.
- Time fell by 5.5% on the hard corpus (all three new runs were faster than all three old ones;
  exact two-sided Mann-Whitney p = 0.1, which is the smallest value possible with 3 runs against
  3) and by 1.4% on the average corpus (p = 0.4). On one machine the shuffle is a local disk
  copy, so most of the saving in bytes does not show as time. On a cluster the shuffle crosses
  the network, which is where this refinement is meant to help; that is not measured here.
- The new `sigs` job costs about as much time as the band job saved: on the hard corpus `sigs`
  took 52.2 s and `bands` 8.5 s, against 70.6 s for the old `bands`.
- Cases where it may fail: a reducer reads the whole `sigs` output once, so its reading time
  grows with the corpus even when few buckets are shared; on a cluster the side input has to be
  read from HDFS (implemented) or shipped with the job; and a corpus full of boilerplate pages
  fills many buckets, which raises the number of signatures each reducer loads (on the hard
  corpus, 14,207 signatures were loaded in all).

## 2. Does collapsing near-duplicates improve the ranked results?

`python -m corpus_jobs.dup_eval --seeds 10 --out results/project/dup_eval.json`

The 149-page Python-docs crawl has one natural near-duplicate pair, too few to measure. The
experiment gives each page a mirror with probability 0.3 (a copy under `/mirror/` on the same
host, with 0.5%, 1% or 2% of its words changed), builds the index and the near-duplicate pairs
with the index job, and loads two search databases from the same output: as it is, and with
near-duplicates collapsed. The 48 judged queries are scored two ways. **Plain**: a mirror is as
relevant as its original. **Group-aware**: a page and its mirror count as one result, so a
result that repeats a page shown higher earns nothing. `redundant@10` counts such repeats in the
top 10. Ten seeds; per query the scores are averaged over the seeds, and the two databases are
compared with the paired randomization test over the 48 queries (10,000 permutations, so the
smallest p-value is 0.0001).

Detection: on average 45.9 mirrors per seed, of which 87.1% were collapsed. One other page per
seed was collapsed: the FastAPI pair that is a natural near-duplicate in the crawl.

| Ranker | Metric | As it is | Collapsed | p |
|---|---|---|---|---|
| TF-IDF | redundant@10 | 2.12 | 0.33 | 0.0001 |
| TF-IDF | group-aware nDCG@10 | 0.520 | 0.560 | 0.0001 |
| TF-IDF | group-aware P@5 | 0.168 | 0.186 | 0.0004 |
| TF-IDF | MRR | 0.545 | 0.564 | 0.0001 |
| TF-IDF | plain nDCG@10 | 0.529 | 0.497 | 0.0243 |
| BM25F + intent + package | redundant@10 | 2.15 | 0.34 | 0.0001 |
| BM25F + intent + package | group-aware nDCG@10 | 0.886 | 0.903 | 0.0001 |
| BM25F + intent + package | group-aware P@5 | 0.266 | 0.273 | 0.0323 |
| BM25F + intent + package | MRR | 0.893 | 0.903 | 0.0034 |
| BM25F + intent + package | plain nDCG@10 | 0.895 | 0.801 | 0.0001 |

- Collapsing removed about 85% of the repeated pages from the top 10 and raised every
  group-aware metric for both rankers.
- The plain metrics go down, because they count a page and its mirror as two relevant results,
  so a list that shows the same page twice scores higher. That is why the group-aware view is
  the one that matches what a user gains.
- MRR is the same in both views, because the first relevant result is never a repeat.
- Limits: the mirrors are synthetic and edited at random, not real version differences; the 48
  judgments are still a draft; and 13% of mirrors were missed, mostly those with 2% of their
  words changed, whose shingle similarity can fall below the 0.8 threshold.

## 3. The Hadoop jobs on a cluster (HDFS and YARN)

`python -m corpus_jobs.cluster start --workers 2` starts a NameNode, a ResourceManager and two
workers, each with its own DataNode and NodeManager. Every daemon is a separate Java process with
its own ports, data directory, PID file and logs. Each worker offers YARN 1 core and 2,304 MB.
With `--hdfs hdfs://localhost:9000/user/cj`, the benchmark submits every Hadoop job to YARN; the
corpus, every intermediate output and the side input of `hadoop-lsh` are on HDFS (replication 2),
and the final output is copied back and compared with the single node's.

**This is not a multi-machine cluster.** All daemons ran on the same 2-vCPU VM, because no second
machine was available. The run checks that the jobs work through the real HDFS and YARN code and
measures what that machinery costs. It cannot show how the jobs scale over machines, and the
shuffle never crosses a real network.

```
export HADOOP_HOME=... JAVA_HOME=...
python -m corpus_jobs.cluster start --workers 2
export HADOOP_CONF_DIR=$PWD/data/cluster/conf-client
python -m corpus_jobs.bench --scenarios average --engines hadoop hadoop-lsh --repeats 3 \
    --hdfs hdfs://localhost:9000/user/cj --work data/m5/out-cluster \
    --out results/project/cluster_bench.json
python -m corpus_jobs.bench --scenarios hard --engines hadoop hadoop-lsh --repeats 1 \
    --hdfs hdfs://localhost:9000/user/cj --work data/m5/out-cluster \
    --out results/project/cluster_bench.json
python -m corpus_jobs.cluster stop
```

| Corpus | Design | Runs | Time (s), median [min-max] | Sum of job times (s) | Band-job shuffle | Total shuffle |
|---|---|---|---|---|---|---|
| average | refined Hadoop | 3 | 169.8 [165.1-171.5] | 150.8 | 336 MB | 543 MB |
| average | hadoop-lsh | 3 | 187.7 [184.8-187.7] | 163.7 | 7.3 MB | 215 MB |
| hard | refined Hadoop | 1 | 354.5 | 330.4 | 1.36 GB | 2.19 GB |
| hard | hadoop-lsh | 1 | 355.5 | 334.4 | 29.4 MB | 0.85 GB |

- Every output (index, top-k, near-duplicate pairs) was identical to the single node's, for
  both designs on both corpora.
- Each YARN job costs about 15 to 20 s before it does any work (starting an application master
  and containers): on a 300-page smoke test every job took 14.6 to 23.5 s. Refined Hadoop has 5
  jobs and `hadoop-lsh` has 6, which is why `hadoop-lsh` was slower here: 187.7 s against
  169.8 s on the average corpus (all three of its runs were slower; exact Mann-Whitney p = 0.1),
  and the same time on the hard corpus (one run each).
- On the same VM, the local job runner was faster than the cluster (71.5 s against 169.8 s on the
  average corpus), because the cluster adds HDFS, YARN scheduling and per-job start-up, and two
  workers share the same two cores.
- The first run of each corpus also uploaded the corpus to HDFS; it is included in that run's
  time and not in the job times.
- What this means for the refinement: its saving is in bytes moved through the shuffle, and on a
  real cluster those bytes cross the network. Whether that outweighs the cost of its extra job
  can only be measured on separate machines, which remains to be done.
