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


def included_entry(rid: str, epoch: int) -> list:
    return ["included", rid, epoch]


def state_leaf(rid: str, state: str) -> list:
    return ["state", rid, state]


def leaf_hash(entry: list) -> str:
    return sha256_hex(canonical(entry))


@dataclass(frozen=True)
class Delta:
    """Status changes (PASS / REVOKED) and moved latest pointers committed by one header.
    INCLUDED never appears in status_changes: a proof in a final block already shows inclusion.
    A ledger that commits a state_root also lists newly final records in `included`, so an
    edge can rebuild the full state map from deltas alone; otherwise `included` is empty and
    the delta (and its root) is exactly the original R1 delta."""

    epoch: int
    status_changes: tuple[tuple[str, str], ...] = ()
    new_latest: tuple[tuple[str, str, str], ...] = ()
    included: tuple[str, ...] = ()

    def entries(self) -> list[list]:
        return (
            [status_entry(rid, s, self.epoch) for rid, s in self.status_changes]
            + [latest_entry(pkg, src, rid, self.epoch) for pkg, src, rid in self.new_latest]
            + [included_entry(rid, self.epoch) for rid in self.included]
        )

    def _leaves(self) -> list[str]:
        return sorted(leaf_hash(e) for e in self.entries())

    def root(self) -> str:
        return merkle_root(self._leaves())

    def branch(self, entry: list) -> list[tuple[str, bool]]:
        leaves = self._leaves()
        return merkle_branch(leaves, leaves.index(leaf_hash(entry)))

    def size_bytes(self) -> int:
        return len(canonical(self.entries()))


class StateTree:
    """Merkle tree over every final record's current state, one leaf per record, in record-ID
    order. Its root is a header's state_root; a branch proves a record's state at that epoch,
    including a state that is not REVOKED (proof of non-revocation)."""

    def __init__(self, states: dict[str, str]):
        self.rids = sorted(states)
        self.index = {rid: i for i, rid in enumerate(self.rids)}
        self.leaves = [leaf_hash(state_leaf(rid, states[rid])) for rid in self.rids]

    def root(self) -> str:
        return merkle_root(self.leaves)

    def branch(self, rid: str) -> list[tuple[str, bool]] | None:
        i = self.index.get(rid)
        return None if i is None else merkle_branch(self.leaves, i)
