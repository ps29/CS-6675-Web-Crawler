"""Block headers and the header chain (M3 Section 3.1, Refinement 1)."""

import json
from dataclasses import asdict, dataclass

from cryptography.exceptions import InvalidSignature

from .merkle import merkle_root
from .record import GENESIS, sha256_hex


@dataclass
class Header:
    prev: str
    epoch: int
    root: str
    proposer: str
    sig: str = ""
    id: str = ""
    delta_root: str = ""  # M4 R1: commits to the epoch's revocation delta

    def compute_id(self) -> str:
        fields = {
            "prev": self.prev,
            "epoch": self.epoch,
            "root": self.root,
            "proposer": self.proposer,
        }
        if self.delta_root:  # absent in M3-only headers, so their IDs are unchanged
            fields["delta_root"] = self.delta_root
        blob = json.dumps(fields, sort_keys=True, separators=(",", ":"))
        return sha256_hex(blob.encode())

    def size_bytes(self) -> int:
        return len(json.dumps(asdict(self), separators=(",", ":")).encode())


def build_header(
    epoch: int, record_ids: list[str], proposer, prev_hash: str, delta_root: str = ""
) -> tuple[Header, list[str]]:
    """BUILD_BLOCK(): sorted ids, Merkle root, signed header. Returns (header, sorted ids)."""
    ids = sorted(record_ids)
    hdr = Header(prev_hash, epoch, merkle_root(ids), proposer.id, delta_root=delta_root)
    hdr.id = hdr.compute_id()
    hdr.sig = proposer.sign(bytes.fromhex(hdr.id)).hex()
    return hdr, ids


class HeaderChain:
    """Headers a peer or consumer has seen; the head is the tip of the longest valid chain."""

    def __init__(self, directory, final_depth: int = 2):
        self.directory = directory
        self.final_depth = final_depth
        self.headers: dict[str, Header] = {}
        self.height: dict[str, int] = {}
        self.head = GENESIS
        self._main: set[str] = set()

    def verify_header(self, hdr: Header) -> bool:
        key = self.directory.public_key(hdr.proposer)
        if key is None or hdr.compute_id() != hdr.id:
            return False
        try:
            key.verify(bytes.fromhex(hdr.sig), bytes.fromhex(hdr.id))
            return True
        except (InvalidSignature, ValueError):
            return False

    def add(self, hdr: Header) -> bool:
        if hdr.id in self.headers:
            return True
        if hdr.prev != GENESIS and hdr.prev not in self.headers:
            return False  # orphan; a real node would buffer it
        if not self.verify_header(hdr):
            return False
        h = self.height.get(hdr.prev, -1) + 1
        self.headers[hdr.id] = hdr
        self.height[hdr.id] = h
        if h > self.height.get(self.head, -1):  # longest chain wins; ties keep the first seen
            self.head = hdr.id
            self._main = set()
            cur = hdr.id
            while cur != GENESIS:
                self._main.add(cur)
                cur = self.headers[cur].prev
        return True

    def contains(self, hdr: Header) -> bool:
        return hdr.id in self._main

    def is_final(self, hdr: Header) -> bool:
        return (
            self.contains(hdr) and self.height[self.head] - self.height[hdr.id] >= self.final_depth
        )
