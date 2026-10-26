"""VERIFY(): record ID, signature, and position in the peer's hash chain."""

from cryptography.exceptions import InvalidSignature

from .merkle import root_from_branch
from .record import GENESIS


def verify_signature(rec, directory) -> bool:
    key = directory.public_key(rec.peer_id)
    if key is None or rec.compute_id() != rec.id:
        return False
    try:
        key.verify(bytes.fromhex(rec.sig), bytes.fromhex(rec.id))
        return True
    except (InvalidSignature, ValueError):
        return False


def chain_is_intact(rec, log) -> bool:
    prev = GENESIS
    for r in log:
        if r.prev != prev or r.compute_id() != r.id:
            return False
        if r.id == rec.id:
            return True
        prev = r.id
    return False


def verify(rec, directory) -> bool:
    return verify_signature(rec, directory) and chain_is_intact(rec, directory.log(rec.peer_id))


def verify_record_r1(rec, branch, hdr, chain, directory) -> bool:
    """VERIFY_RECORD(): record signature, header signature, Merkle branch and chain membership.
    No peer log needed; the branch left/right flags carry the leaf index."""
    return (
        verify_signature(rec, directory)
        and chain.verify_header(hdr)
        and root_from_branch(rec.id, branch) == hdr.root
        and chain.contains(hdr)
    )
