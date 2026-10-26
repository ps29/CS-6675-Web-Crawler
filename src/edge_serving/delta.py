"""Per-epoch revocation delta committed by a header's delta_root (M4 Refinement 1)."""

from __future__ import annotations

from dataclasses import dataclass

from crawl_ledger.merkle import merkle_branch, merkle_root
from crawl_ledger.record import sha256_hex

from .objects import canonical


def status_entry(rid: str, state: str, epoch: int) -> list:
    return ["status", rid, state, epoch]


def latest_entry(pkg: str, src: str, rid: str, epoch: int) -> list:
    return ["latest", pkg, src, rid, epoch]


def leaf_hash(entry: list) -> str:
    return sha256_hex(canonical(entry))


@dataclass(frozen=True)
class Delta:
    """Status changes (PASS / REVOKED) and moved latest pointers committed by one header.
    INCLUDED is never listed: a proof in a final block already shows inclusion."""

    epoch: int
    status_changes: tuple[tuple[str, str], ...] = ()
    new_latest: tuple[tuple[str, str, str], ...] = ()

    def entries(self) -> list[list]:
        return [status_entry(rid, s, self.epoch) for rid, s in self.status_changes] + [
            latest_entry(pkg, src, rid, self.epoch) for pkg, src, rid in self.new_latest
        ]

    def _leaves(self) -> list[str]:
        return sorted(leaf_hash(e) for e in self.entries())

    def root(self) -> str:
        return merkle_root(self._leaves())

    def branch(self, entry: list) -> list[tuple[str, bool]]:
        leaves = self._leaves()
        return merkle_branch(leaves, leaves.index(leaf_hash(entry)))

    def size_bytes(self) -> int:
        return len(canonical(self.entries()))
