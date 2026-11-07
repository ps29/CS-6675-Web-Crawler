"""Dishonest edges for the M4 evaluation: attacks a client cannot catch (M4 Section 3.1).

An edge cannot forge proofs, signed statuses or delta entries, so these attackers only hide
revocations. The baseline attacker replays an older signed status, which max_lag bounds. The R1
attackers serve a status with no signed freshness: `freeze` stops applying deltas and overstates
synced_epoch, and `withhold` stays synced, reports it truthfully and drops revocation entries."""

from __future__ import annotations

from crawl_ledger.blocks import Header

from .delta import Delta
from .edge import Edge
from .objects import REVOKED, Response
from .revocation import DeltaSource, R1Edge
from .summary import R1R2Edge, R2Edge

WITHHOLD, FREEZE = "withhold", "freeze"
ATTACKS = (WITHHOLD, FREEZE)


class StatusReplay:
    """Mixin for Edge / R2Edge: replay the last signed status from before a revocation."""

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


class ReplayEdge(StatusReplay, Edge):
    pass


class ReplayR2Edge(StatusReplay, R2Edge):
    pass


class LyingR1Edge(LyingR1, R1Edge):
    pass


class LyingR1R2Edge(LyingR1, R1R2Edge):
    pass
