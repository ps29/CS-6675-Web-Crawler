"""Dishonest edges for the M4 evaluation: attacks a client cannot catch (M4 Section 3.1).

An edge cannot forge proofs, signed statuses or delta entries, so these attackers only hide
revocations. The baseline attacker replays an older signed status, which max_lag bounds. The R1
attackers serve a status with no signed freshness: `freeze` stops applying deltas and overstates
synced_epoch, and `withhold` stays synced, reports it truthfully and drops revocation entries.
`replay` keeps serving the last status it held before a revocation.

Against R1-S edges (state_proofs.py) the same attacks apply: `withhold` now yields a state branch
that no longer matches any header, `freeze` and `replay` yield a valid branch for an old epoch,
which the client refetches once it is more than max_lag epochs old."""

from __future__ import annotations

from crawl_ledger.blocks import Header

from .delta import Delta
from .edge import Edge
from .objects import REVOKED, Response
from .revocation import DeltaSource, R1Edge
from .state_proofs import StateProofEdge
from .summary import R1R2Edge, R2Edge

WITHHOLD, FREEZE, REPLAY = "withhold", "freeze", "replay"
ATTACKS = (WITHHOLD, FREEZE, REPLAY)


class StatusReplay:
    """Mixin for any edge: replay the last status it served from before a revocation."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_good: dict[str, Response] = {}

    def get(self, path: str) -> Response:
        resp = super().get(path)
        if not path.startswith("/status/") or resp.status != 200:
            return resp
        if resp.body["state"] == REVOKED:
            return self.last_good.get(path, resp)
        self.last_good[path] = resp
        return resp


class LyingR1:
    """Mixin for R1Edge / R1R2Edge. Honest until `active`, so it can bootstrap from gossip."""

    def __init__(self, *args, attack: str = WITHHOLD, **kwargs):
        super().__init__(*args, **kwargs)
        self.attack = attack
        self.active = False
        self.claimed_epoch = -1

    def on_header(self, hdr: Header, delta: Delta, fetch_gap: DeltaSource) -> bool:
        self.claimed_epoch = max(self.claimed_epoch, hdr.epoch)
        if self.active and self.attack == FREEZE:
            return True
        return super().on_header(hdr, delta, fetch_gap)

    def _apply(self, hdr: Header, delta: Delta) -> bool:
        revoked = [rid for rid, state in delta.status_changes if state == REVOKED]
        prior = {rid: self.statuses.get(rid) for rid in revoked}
        applied = super()._apply(hdr, delta)
        if self.active and self.attack == WITHHOLD:
            for rid, status in prior.items():
                if status is None:
                    self.statuses.pop(rid, None)
                else:
                    self.statuses[rid] = status
        return applied

    def stamp(self, body: dict) -> dict:
        return {**body, "synced_epoch": self.claimed_epoch}


class LyingStateProof(LyingR1):
    """LyingR1 for R1-S edges: `withhold` also keeps revoked records out of the state map."""

    def _apply(self, hdr: Header, delta: Delta) -> bool:
        revoked = [rid for rid, state in delta.status_changes if state == REVOKED]
        prior = {rid: self.state.get(rid) for rid in revoked}
        applied = super()._apply(hdr, delta)
        if self.active and self.attack == WITHHOLD:
            for rid, state in prior.items():
                if state is None:
                    self.state.pop(rid, None)
                else:
                    self.state[rid] = state
            self._tree = None
        return applied


class ReplayEdge(StatusReplay, Edge):
    pass


class ReplayR2Edge(StatusReplay, R2Edge):
    pass


class LyingR1Edge(LyingR1, R1Edge):
    pass


class LyingR1R2Edge(LyingR1, R1R2Edge):
    pass


class ReplayR1Edge(StatusReplay, R1Edge):
    pass


class ReplayR1R2Edge(StatusReplay, R1R2Edge):
    pass


class LyingStateProofEdge(LyingStateProof, StateProofEdge):
    pass


class ReplayStateProofEdge(StatusReplay, StateProofEdge):
    pass
