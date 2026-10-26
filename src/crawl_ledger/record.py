"""Crawl records: canonical encoding, ID and Ed25519 signatures (M3 Section 2.2/2.4)."""

import hashlib
import json
from dataclasses import asdict, dataclass

SIGNED_FIELDS = ("peer_id", "pkg", "source", "url", "hash", "time", "prev")
GENESIS = "0" * 64


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Record:
    peer_id: str
    pkg: str
    source: str
    url: str
    hash: str  # SHA-256 of the fetched content
    time: float  # fetch time (Unix seconds)
    prev: str  # id of the previous record in this peer's log
    id: str = ""
    sig: str = ""

    def signed_fields(self) -> dict:
        return {k: getattr(self, k) for k in SIGNED_FIELDS}

    def compute_id(self) -> str:
        blob = json.dumps(self.signed_fields(), sort_keys=True, separators=(",", ":"))
        return sha256_hex(blob.encode())

    def size_bytes(self) -> int:
        return len(json.dumps(asdict(self), separators=(",", ":")).encode())
