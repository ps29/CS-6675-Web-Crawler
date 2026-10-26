"""Merkle tree over record IDs (M3 Section 3.1). An odd level duplicates its last node."""

import hashlib

from .record import GENESIS


def _h(a: bytes, b: bytes) -> bytes:
    return hashlib.sha256(a + b).digest()


def _next_level(level: list[bytes]) -> list[bytes]:
    if len(level) % 2:
        level = level + [level[-1]]
    return [_h(level[i], level[i + 1]) for i in range(0, len(level), 2)]


def merkle_root(ids: list[str]) -> str:
    if not ids:
        return GENESIS
    level = [bytes.fromhex(i) for i in ids]
    while len(level) > 1:
        level = _next_level(level)
    return level[0].hex()


def merkle_branch(ids: list[str], index: int) -> list[tuple[str, bool]]:
    """Sibling hashes from leaf to root.

    Each entry is (hash, sibling_is_left), so the leaf index is implicit."""
    level = [bytes.fromhex(i) for i in ids]
    branch, idx = [], index
    while len(level) > 1:
        if len(level) % 2:
            level = level + [level[-1]]
        sib = idx ^ 1
        branch.append((level[sib].hex(), sib < idx))
        level = _next_level(level)
        idx //= 2
    return branch


def root_from_branch(leaf_id: str, branch) -> str:
    node = bytes.fromhex(leaf_id)
    for sib, sib_is_left in branch:
        s = bytes.fromhex(sib)
        node = _h(s, node) if sib_is_left else _h(node, s)
    return node.hex()
