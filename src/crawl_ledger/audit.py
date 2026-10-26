"""AUDIT(): verify the record, re-fetch the page, compare hashes (M3 Section 2.4)."""

from .record import sha256_hex
from .verify import verify

PASS, FAIL, INCONCLUSIVE = "PASS", "FAIL", "INCONCLUSIVE"


def source_changed_since(result, rec_time: float) -> bool:
    # The source's own signal: newest PyPI upload time, or HTTP Last-Modified.
    return result.changed_at is not None and result.changed_at > rec_time


def audit(rec, directory, fetcher) -> str:
    if not verify(rec, directory):
        return FAIL
    result = fetcher.fetch(rec.url)
    if result is None or result.status != 200:
        return INCONCLUSIVE
    if sha256_hex(result.content) == rec.hash:
        return PASS
    if source_changed_since(result, rec.time):
        return INCONCLUSIVE
    return FAIL
