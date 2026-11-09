"""The record-level logic every engine shares: tokenizing, postings, MinHash and LSH bands.

The single-node baseline, the Hadoop Streaming jobs and the Spark pipeline all call these
functions, so their outputs can be compared line by line and any difference in run time or
shuffle volume comes from how a job is executed, not from what it computes.

Tokens are lowercased runs of letters and digits, 2-40 characters long. Unlike the M1 indexer
(`focused_crawler.indexer`), there is no stemming: it costs the same in every engine and would
only add a constant to every measurement.

MinHash (Broder 1997) uses NUM_PERM universal hash functions h(x) = (a*x + b) mod (2^31 - 1) over
32-bit shingle hashes (CRC-32 of each SHINGLE-word window, which is stable across processes, unlike
Python's `hash`). LSH splits a signature into BANDS bands of ROWS rows; two documents become a
candidate pair when any band matches, which happens with probability 1 - (1 - J^ROWS)^BANDS for
Jaccard similarity J (about 0.5 at J = 0.7 and 0.98 at J = 0.9 with 16 x 8)."""

from __future__ import annotations

import json
import re
import zlib
from collections import Counter

import numpy as np

TOKEN = re.compile(r"[^\W_]{2,40}")
SHINGLE = 5
NUM_PERM = 128
BANDS, ROWS = 16, 8
assert BANDS * ROWS == NUM_PERM
THRESHOLD = 0.8  # estimated Jaccard at or above which a candidate pair is a near-duplicate
MAX_BUCKET = 200  # LSH buckets larger than this (boilerplate pages) are skipped, and counted
_rng = np.random.default_rng(6675)  # fixed seed: every process draws the same hash family
# Universal hashing modulo the Mersenne prime 2^31 - 1, so (a * x + b) fits in int64.
_P31 = (1 << 31) - 1
_A31 = _rng.integers(1, _P31, NUM_PERM, dtype=np.int64)
_B31 = _rng.integers(0, _P31, NUM_PERM, dtype=np.int64)


def parse(line: str) -> tuple[str, str]:
    """One corpus record: a JSON line with "doc" and "text"."""
    rec = json.loads(line)
    return rec["doc"], rec["text"]


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def term_freqs(toks: list[str]) -> Counter:
    return Counter(toks)


def shingles(toks: list[str]) -> np.ndarray:
    """CRC-32 hashes of the distinct SHINGLE-word windows (one window for short docs)."""
    if not toks:
        return np.zeros(0, dtype=np.int64)
    n = max(1, len(toks) - SHINGLE + 1)
    hs = {zlib.crc32(" ".join(toks[i : i + SHINGLE]).encode()) for i in range(n)}
    return np.fromiter(hs, dtype=np.int64, count=len(hs))


def signature(toks: list[str]) -> np.ndarray | None:
    """NUM_PERM-value MinHash signature, or None for a document without tokens."""
    sh = shingles(toks)
    if sh.size == 0:
        return None
    sh = sh % _P31
    # (NUM_PERM x |shingles|) matrix of hashes; the row minima are the signature
    return ((np.outer(_A31, sh) + _B31[:, None]) % _P31).min(axis=1)


def bands(sig: np.ndarray) -> list[str]:
    """One bucket key per band: band index plus a hash of that band's rows."""
    out = []
    for b in range(BANDS):
        rows = sig[b * ROWS : (b + 1) * ROWS]
        out.append(f"{b}:{zlib.crc32(rows.tobytes()):08x}")
    return out


def sig_to_str(sig: np.ndarray) -> str:
    return ",".join(map(str, sig.tolist()))


def sig_from_str(s: str) -> np.ndarray:
    return np.array(s.split(","), dtype=np.int64)


def estimate(a: np.ndarray, b: np.ndarray) -> float:
    """Estimated Jaccard similarity: the share of signature positions that agree."""
    return float(np.mean(a == b))


def exact_jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(shingles(a).tolist()), set(shingles(b).tolist())
    return len(sa & sb) / len(sa | sb) if sa or sb else 1.0


def bucket_pairs(members: list[tuple[str, np.ndarray]]) -> list[tuple[str, str, float]]:
    """Candidate pairs of one LSH bucket with their estimated similarity (>= THRESHOLD)."""
    out = []
    members = sorted(members, key=lambda m: m[0])
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            s = estimate(members[i][1], members[j][1])
            if s >= THRESHOLD:
                out.append((members[i][0], members[j][0], s))
    return out


def postings_line(term: str, postings: list[tuple[str, int]]) -> str:
    """Canonical output line of the inverted index: term, df, then doc:tf sorted by doc."""
    ps = sorted(postings)
    return f"{term}\t{len(ps)}\t" + ",".join(f"{d}:{tf}" for d, tf in ps)


def pair_line(a: str, b: str, sim: float) -> str:
    return f"{a}\t{b}\t{sim:.4f}"
