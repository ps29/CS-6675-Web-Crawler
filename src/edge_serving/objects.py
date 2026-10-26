"""Served objects and their cache rules (M4 Section 2.3, Table 2)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from crawl_ledger.blocks import Header
from crawl_ledger.record import Record

PENDING, INCLUDED, PASS, REVOKED = "PENDING", "INCLUDED", "PASS", "REVOKED"
STATES = (PENDING, INCLUDED, PASS, REVOKED)

ONE_YEAR = 31_536_000
IMMUTABLE = f"max-age={ONE_YEAR}, immutable"
NO_STORE = "no-store"


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class Response:
    status: int
    body: dict
    cache_control: str = NO_STORE

    def size_bytes(self) -> int:
        return len(canonical(self.body))


@dataclass(frozen=True)
class Proof:
    """Record, signature, Merkle branch and block header: never changes once its block is final."""

    record: Record
    branch: list[tuple[str, bool]]
    header: Header

    def to_dict(self) -> dict:
        return {
            "record": asdict(self.record),
            "branch": [[h, left] for h, left in self.branch],
            "header": asdict(self.header),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Proof:
        return cls(
            Record(**d["record"]),
            [(h, bool(left)) for h, left in d["branch"]],
            Header(**d["header"]),
        )


@dataclass(frozen=True)
class Status:
    """Mutable trust state of a record, signed by the gateway (baseline)."""

    rid: str
    state: str
    reputation: float
    epoch: int
    sig: str = ""

    def signed_bytes(self) -> bytes:
        return canonical(
            {"rid": self.rid, "state": self.state, "rep": self.reputation, "epoch": self.epoch}
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Status:
        return cls(**{k: d[k] for k in ("rid", "state", "reputation", "epoch", "sig")})


def parse_cache_control(value: str) -> dict[str, int | bool]:
    out: dict[str, int | bool] = {}
    for part in value.split(","):
        name, _, arg = part.strip().partition("=")
        if name:
            out[name.lower()] = int(arg) if arg.isdigit() else True
    return out


def shared_cache_lifetime(cache_control: str) -> float | None:
    """Seconds a shared cache may serve the response; None means do not store it."""
    cc = parse_cache_control(cache_control)
    if cc.get("no-store") or cc.get("private"):
        return None
    for key in ("s-maxage", "max-age"):
        if key in cc and cc[key] is not True:
            return float(cc[key])
    return None


@dataclass
class CacheEntry:
    response: Response
    stored_at: float
    lifetime: float = field(default=0.0)

    def fresh(self, now: float) -> bool:
        return now - self.stored_at < self.lifetime
