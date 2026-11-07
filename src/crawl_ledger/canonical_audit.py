"""CANONICAL_AUDIT(): audits that tolerate build noise but not edits to sensitive content.

Verdicts, in order:
1. FAIL if the record's signature or chain is invalid, or the claimed block hashes do not hash
   to the signed `canon` root.
2. INCONCLUSIVE if the page cannot be fetched.
3. PASS if the auditor's canonical digest has the same root as the claim.
4. INCONCLUSIVE if the auditor's own two fetches disagree (the page itself is unstable).
5. INCONCLUSIVE if the source says it changed after the record was made.
6. FAIL if the difference touches a sensitive block (code, a link or script source, an install
   command or version string, a sensitive metadata field), or the claim has blocks the page lacks
   that a replaced block cannot explain. Otherwise INCONCLUSIVE: an edit to ordinary prose is
   flagged for re-audit, not proven malicious.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field

from .audit import FAIL, INCONCLUSIVE, PASS, source_changed_since
from .canonical import Block, Digest, digest
from .merkle import merkle_root
from .verify import verify


@dataclass
class AuditDetail:
    verdict: str
    reason: str
    unmatched_observed: list[Block] = field(default_factory=list)
    unmatched_claimed: int = 0


def claim_root(version: int, hashes: list[str]) -> str:
    """Root of a claimed block list; equal to Digest.root for the same blocks."""
    return merkle_root([hashlib.sha256(f"canon-v{version}".encode()).hexdigest()] + hashes)


def compare(claimed: list[str], observed: Digest) -> tuple[list[Block], int]:
    """Blocks the auditor saw that the claim lacks, and how many claimed hashes it did not see."""
    want = Counter(claimed)
    extra: list[Block] = []
    for b in observed.blocks:
        h = b.digest()
        if want[h] > 0:
            want[h] -= 1
        else:
            extra.append(b)
    return extra, sum(want.values())


def judge(claimed: list[str], first: Digest) -> AuditDetail:
    extra, missing = compare(claimed, first)
    if any(b.sensitive for b in extra):
        return AuditDetail(FAIL, "sensitive block differs", extra, missing)
    if missing > len(extra):
        return AuditDetail(FAIL, "claim has blocks the page lacks", extra, missing)
    return AuditDetail(INCONCLUSIVE, "only ordinary text differs", extra, missing)


def decide(
    claimed: list[str], version: int, first: bytes | None, second: bytes | None, changed: bool
) -> AuditDetail:
    """Steps 2-6 on fetched content (None = the fetch failed); `changed` is step 5's signal."""
    if first is None:
        return AuditDetail(INCONCLUSIVE, "fetch failed")
    d2 = digest(second) if second is not None else None
    return decide_digests(claimed, version, digest(first), d2.root if d2 else None, changed)


def decide_digests(
    claimed: list[str], version: int, d1: Digest, second_root: str | None, changed: bool
) -> AuditDetail:
    """decide() on already computed digests of the auditor's two fetches."""
    if d1.version != version:
        return AuditDetail(INCONCLUSIVE, "canonicalizer version differs")
    if d1.root == claim_root(version, claimed):
        return AuditDetail(PASS, "canonical digest matches")
    if second_root is None or second_root != d1.root:
        return AuditDetail(INCONCLUSIVE, "auditor's own fetches disagree")
    if changed:
        return AuditDetail(INCONCLUSIVE, "source changed after the record")
    return judge(claimed, d1)


def canonical_audit(rec, claimed: list[str], version: int, directory, fetcher) -> AuditDetail:
    if not verify(rec, directory) or not rec.canon or claim_root(version, claimed) != rec.canon:
        return AuditDetail(FAIL, "invalid record or claim")
    r1 = fetcher.fetch(rec.url)
    if r1 is None or r1.status != 200:
        return AuditDetail(INCONCLUSIVE, "fetch failed")
    d1 = digest(r1.content)
    if d1.root == rec.canon:
        return AuditDetail(PASS, "canonical digest matches")
    r2 = fetcher.fetch(rec.url)  # only on a mismatch; the fetcher keeps the per-host delay
    second = r2.content if r2 is not None and r2.status == 200 else None
    return decide(claimed, version, r1.content, second, source_changed_since(r1, rec.time))
