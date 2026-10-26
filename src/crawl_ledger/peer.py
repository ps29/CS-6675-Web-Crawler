"""A peer: key pair, node ID, and its append-only, hash-chained record log."""

import random
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .record import GENESIS, Record, sha256_hex


class Peer:
    def __init__(self, addr: str, forge_rate: float = 0.0, rng: random.Random | None = None):
        self._sk = Ed25519PrivateKey.generate()
        self.public_key = self._sk.public_key()
        pk_raw = self.public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.addr = addr
        # Node ID as defined in the course lecture: hash(public key + IP address).
        self.id = sha256_hex(pk_raw + addr.encode())
        self.log: list[Record] = []
        self.forge_rate = forge_rate  # 0 for honest peers
        self.rng = rng or random.Random()

    @property
    def honest(self) -> bool:
        return self.forge_rate == 0

    @property
    def head_hash(self) -> str:
        return self.log[-1].id if self.log else GENESIS

    def sign(self, data: bytes) -> bytes:
        return self._sk.sign(data)

    def make_record(
        self, pkg: str, source: str, url: str, content: bytes, now: float | None = None
    ):
        """RECORD(): hash the content, link to the previous record, sign, append.
        A dishonest peer reports a hash of altered content with probability forge_rate.
        Returns (record, forged_flag); the flag is ground truth for the experiment only."""
        forged = self.rng.random() < self.forge_rate
        body = content + b"\n<!-- altered install command -->" if forged else content
        rec = Record(
            self.id,
            pkg,
            source,
            url,
            sha256_hex(body),
            time.time() if now is None else now,
            self.head_hash,
        )
        rec.id = rec.compute_id()
        rec.sig = self._sk.sign(bytes.fromhex(rec.id)).hex()
        self.log.append(rec)
        return rec, forged


class Directory:
    """Who is who: peer_id -> peer (public key and gossiped log)."""

    def __init__(self, peers):
        self.by_id = {p.id: p for p in peers}

    def public_key(self, peer_id):
        p = self.by_id.get(peer_id)
        return p.public_key if p else None

    def log(self, peer_id):
        p = self.by_id.get(peer_id)
        return p.log if p else []
