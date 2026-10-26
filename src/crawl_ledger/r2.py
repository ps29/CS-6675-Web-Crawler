"""Refinement 2: deterministic auditors and reputation (M3 Section 3.2)."""

from .audit import FAIL, INCONCLUSIVE, PASS
from .config import Config
from .record import sha256_hex


def auditors(rec, later_hash: str, ring, s: int):
    """AUDITORS(): the s ring successors of SHA256(later_hash + rec.id), excluding the owner."""
    seed = sha256_hex((later_hash + rec.id).encode())
    return ring.successors(seed, s, exclude=rec.peer_id)


def final_verdict(results) -> str:
    """Majority of PASS/FAIL votes, ignoring INCONCLUSIVE ones.

    A tie or no votes is INCONCLUSIVE."""
    p, f = results.count(PASS), results.count(FAIL)
    if p == f:
        return INCONCLUSIVE
    return PASS if p > f else FAIL


class Reputation:
    def __init__(self, peer_ids, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.rep = {pid: 1.0 for pid in peer_ids}
        self.audited = {pid: 0 for pid in peer_ids}
        self.inconclusive = {pid: 0 for pid in peer_ids}

    def settle(self, owner_id: str, results) -> str:
        """SETTLE(): only the combined verdict changes the owner's reputation."""
        verdict = final_verdict(results)
        self.audited[owner_id] += 1
        if verdict == FAIL:
            self.rep[owner_id] *= self.cfg.fail_factor
        elif verdict == PASS:
            self.rep[owner_id] = min(1.0, self.rep[owner_id] + self.cfg.pass_gain)
        else:
            self.inconclusive[owner_id] += 1
        return verdict

    def eligible(self, peer_id: str) -> bool:
        return self.rep[peer_id] >= self.cfg.eligibility

    def flagged(self) -> set[str]:
        """Peers whose share of inconclusive audits is far above the average."""
        rates = {
            p: self.inconclusive[p] / self.audited[p]
            for p in self.rep
            if self.audited[p] >= self.cfg.min_audits
        }
        if not rates:
            return set()
        mean = sum(rates.values()) / len(rates)
        return {p for p, r in rates.items() if r > mean + self.cfg.inconc_margin}
