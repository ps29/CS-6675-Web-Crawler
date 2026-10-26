"""Client verifier: CLIENT_CHECK (M4 Section 2.4) with per-consumer trust policies."""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from crawl_ledger.blocks import HeaderChain
from crawl_ledger.peer import Directory
from crawl_ledger.verify import verify_record_r1

from .objects import INCLUDED, PASS, Proof, Status

ACCEPT, REJECT, REFETCH = "ACCEPT", "REJECT", "REFETCH"


@dataclass(frozen=True)
class Policy:
    accept: frozenset[str]
    max_lag: int


IDE_POLICY = Policy(frozenset({INCLUDED, PASS}), max_lag=3)
RAG_POLICY = Policy(frozenset({PASS}), max_lag=1)


class Client:
    def __init__(
        self,
        chain: HeaderChain,
        directory: Directory,
        gateway_key: Ed25519PublicKey,
        policy: Policy = IDE_POLICY,
    ):
        self.chain = chain
        self.directory = directory
        self.gateway_key = gateway_key
        self.policy = policy

    def head_epoch(self) -> int:
        hdr = self.chain.headers.get(self.chain.head)
        return hdr.epoch if hdr else -1

    def verify_proof(self, proof: Proof) -> bool:
        return verify_record_r1(
            proof.record, proof.branch, proof.header, self.chain, self.directory
        )

    def check(self, proof: Proof, status: Status) -> str:
        if not self.verify_proof(proof) or status.rid != proof.record.id:
            return REJECT
        try:
            self.gateway_key.verify(bytes.fromhex(status.sig), status.signed_bytes())
        except (InvalidSignature, ValueError):
            return REJECT
        if self.head_epoch() - status.epoch > self.policy.max_lag:
            return REFETCH
        return ACCEPT if status.state in self.policy.accept else REJECT

    def check_latest(self, body: dict) -> str:
        """Check an EDGE_GET_LATEST response body."""
        try:
            proof = Proof.from_dict(body["proof"])
            status = Status.from_dict(body["status"])
        except (KeyError, TypeError):
            return REJECT
        return self.check(proof, status)
