"""R1-S: Refinement 1 with proofs of non-revocation (follow-up to M4 Section 3.1).

Plain R1 lets a dishonest edge hide a revocation indefinitely: a status is a delta entry, and an
absent entry ("implied INCLUDED") or an old PASS entry proves nothing about later epochs.

Here each header also commits a state_root: a Merkle root over every final record's current
state. The edge rebuilds the state map from deltas (which now also list newly final records) and
serves a status as (state, epoch, branch to that epoch's state_root). The client checks the
branch against its own header chain and the freshness of the *proven* epoch, not the edge's
self-reported synced_epoch. An edge can then only replay a state that was true at most
`max_lag` epochs ago, the same bound the baseline gets from the gateway's signed epoch, without
trusting the gateway's key."""

from __future__ import annotations

from crawl_ledger.blocks import Header
from crawl_ledger.merkle import root_from_branch

from .client import ACCEPT, REFETCH, REJECT
from .delta import Delta, StateTree, leaf_hash, state_leaf
from .objects import INCLUDED, NO_STORE, PENDING, Proof, Response
from .revocation import R1Client, R1Edge


class StateProofEdge(R1Edge):
    """R1 edge that also tracks every final record's state and serves state branches."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state: dict[str, str] = {}
        self._tree: StateTree | None = None
        self._tree_epoch = -1
        self.root_mismatches = 0

    def _apply(self, hdr: Header, delta: Delta) -> bool:
        if not super()._apply(hdr, delta):
            return False
        for rid in delta.included:
            self.state.setdefault(rid, INCLUDED)
        for rid, state in delta.status_changes:
            self.state[rid] = state
        self._tree = None
        return True

    def tree(self) -> StateTree:
        if self._tree is None or self._tree_epoch != self.synced_epoch:
            self._tree = StateTree(self.state)
            self._tree_epoch = self.synced_epoch
            hdr = self.chain.headers.get(self.chain.head)
            if hdr is not None and hdr.state_root and self._tree.root() != hdr.state_root:
                self.root_mismatches += 1  # this edge's state diverged from the ledger
        return self._tree

    def get(self, path: str) -> Response:
        if self.ttl_fallback or not path.startswith("/status/"):
            return super().get(path)
        self.stats.hits += 1
        rid = path.strip("/").split("/")[1]
        state = self.state.get(rid)
        if state is None:  # not final yet: nothing to prove, and no policy accepts PENDING
            return self._served(Response(200, {"rid": rid, "state": PENDING}, NO_STORE))
        body = {
            "rid": rid,
            "state": state,
            "epoch": self.synced_epoch,
            "state_branch": [[h, left] for h, left in self.tree().branch(rid)],
        }
        return self._served(Response(200, body, NO_STORE))


class StateProofClient(R1Client):
    """CHECK_STATUS_R1S: the state branch must hash to state_root of the client's own header at
    the stated epoch, and that epoch must be within max_lag of the client's head."""

    def check_state(self, proof: Proof, status: dict) -> str:
        if not self.verify_proof(proof) or status.get("rid") != proof.record.id:
            return REJECT
        try:
            epoch = int(status["epoch"])
            state = status["state"]
            branch = [(h, bool(left)) for h, left in status["state_branch"]]
        except (KeyError, TypeError, ValueError):
            return REJECT
        hdr = self.header_at(epoch)
        if hdr is None or not hdr.state_root:
            return REJECT
        if root_from_branch(leaf_hash(state_leaf(proof.record.id, state)), branch) != (
            hdr.state_root
        ):
            return REJECT
        if self.head_epoch() - epoch > self.policy.max_lag:
            return REFETCH
        return ACCEPT if state in self.policy.accept else REJECT

    def check_latest(self, body: dict) -> str:
        try:
            proof = Proof.from_dict(body["proof"])
            return self.check_state(proof, body["status"])
        except (KeyError, TypeError, ValueError):
            return REJECT
