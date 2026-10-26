"""Stateless verification gateway (tier 3): GATEWAY_GET for /latest, /proof and /status."""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .ledger_view import Ledger
from .objects import IMMUTABLE, NO_STORE, PENDING, Response, Status


class Gateway:
    def __init__(
        self,
        ledger: Ledger,
        t_ptr: float = 60.0,
        t_status: float = 600.0,
        key: Ed25519PrivateKey | None = None,
    ):
        self.ledger = ledger
        self.t_ptr = int(t_ptr)
        self.t_status = int(t_status)
        self._key = key or Ed25519PrivateKey.generate()
        self.public_key = self._key.public_key()
        self.requests = 0
        self.lookups = 0

    def get(self, path: str) -> Response:
        self.requests += 1
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "latest":
            return self._latest(parts[1], parts[2])
        if len(parts) == 2 and parts[0] == "proof":
            return self._proof(parts[1])
        if len(parts) == 2 and parts[0] == "status":
            return self._status(parts[1])
        return Response(400, {"error": "unknown path"})

    def _latest(self, pkg: str, src: str) -> Response:
        rid = self.ledger.latest.get((pkg, src))
        if rid is None:
            return Response(404, {"error": "not crawled yet"})
        return Response(200, {"rid": rid}, f"s-maxage={self.t_ptr}")

    def _proof(self, rid: str) -> Response:
        if rid not in self.ledger.records:
            # A Chord routing error would also look like this, so never cache it (M4 Section 4.3).
            return Response(404, {"error": "unknown record"})
        self.lookups += 1  # one Chord lookup + ledger read per proof built
        if not self.ledger.is_final(rid):
            return Response(202, {"state": PENDING}, NO_STORE)
        return Response(200, self.ledger.proof(rid).to_dict(), IMMUTABLE)

    def _status(self, rid: str) -> Response:
        if rid not in self.ledger.records:
            return Response(404, {"error": "unknown record"})
        status = self.sign_status(rid)
        return Response(200, status.to_dict(), f"s-maxage={self.t_status}")

    def sign_status(self, rid: str) -> Status:
        rec = self.ledger.records[rid]
        unsigned = Status(
            rid,
            self.ledger.state[rid],
            self.ledger.rep(rec.peer_id),
            self.ledger.head_epoch(),
        )
        sig = self._key.sign(unsigned.signed_bytes()).hex()
        return Status(unsigned.rid, unsigned.state, unsigned.reputation, unsigned.epoch, sig)
