"""Crawl records: canonical encoding, ID and Ed25519 signatures (M3 Section 2.2/2.4)."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass

SIGNED_FIELDS = ("peer_id", "pkg", "source", "url", "hash", "time", "prev")
GENESIS = "0" * 64

# Tokens Cloudflare rewrites in every response, seen on matplotlib.org (Rocket Loader) and
# docs.sqlalchemy.org (challenge script). Only the token goes: script paths and comments stay,
# so a forged page still hashes differently.
CDN_NONCES = (
    (re.compile(rb'type="[0-9a-f]{24}-'), b'type="'),
    (re.compile(rb'data-cf-settings="[0-9a-f]{24}-\|\d+"'), b'data-cf-settings=""'),
    (re.compile(rb"__CF\$cv\$params=\{r:'[0-9a-f]+',t:'[A-Za-z0-9+/=]+'\}"), b"__CF$cv$params={}"),
    (re.compile(rb"(/cdn-cgi/challenge-platform/scripts/jsd/main\.js)\?ray=[0-9a-f]+"), rb"\1"),
)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_content(content: bytes) -> bytes:
    for pattern, replacement in CDN_NONCES:
        content = pattern.sub(replacement, content)
    return content


def content_hash(content: bytes) -> str:
    return sha256_hex(canonical_content(content))


@dataclass
class Record:
    peer_id: str
    pkg: str
    source: str
    url: str
    hash: str  # SHA-256 of the fetched content, CDN nonces removed
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
