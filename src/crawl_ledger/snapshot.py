"""Ledger snapshot: the records of a crawl with their content and audit verdicts, written so
that another program can check every record again without trusting whoever wrote the files.

    python -m crawl_ledger.snapshot --limit 25 --audit-rate 0.5 --out data/project/ledger
    python -m crawl_ledger.snapshot --offline --out /tmp/ledger     # no network, test content

This is the link from the crawl ledger (M3) to the index job (M5). A snapshot directory holds:

- `peers.json`: each peer's address and Ed25519 public key;
- `records.jsonl`: every published record in publication order, with the verdict of its audit
  (PASS, FAIL, INCONCLUSIVE, or null if it was not sampled for an audit);
- `content/<hash>`: the body each record's hash was computed over.

`accepted()` reads a snapshot and decides, record by record, whether its content may be indexed.
It trusts nothing in the files: it recomputes each peer's node ID, each record's ID, signature
and position in the peer's hash chain, and the hash of the stored content. Then a policy is
applied to the audit verdict:

- `pass`: only records whose audit returned PASS (an unaudited record is left out);
- `not-failed`: every record except those whose audit returned FAIL.

`records.jsonl` also carries `forged`, the experiment's ground truth. `accepted()` reports how
many forged records a policy admitted, and never uses the flag to decide.

The audit verdicts in the file are not signed, so a reader has to trust the auditor that wrote
them. In the M3 refinements, verdicts are settled on the header chain; that is not exported here.
"""

from __future__ import annotations

import json
import random
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from runinfo import provenance

from .audit import FAIL, PASS, audit
from .fetcher import Fetcher, OfflineFetcher
from .peer import Directory, Peer
from .record import GENESIS, Record, content_hash, sha256_hex
from .ring import Ring
from .sim import build_parser as sim_parser
from .sim import crawl, load_seeds
from .verify import verify

POLICIES = ("pass", "not-failed")
RECORD_FIELDS = ("peer_id", "pkg", "source", "url", "hash", "time", "prev", "id", "sig", "canon")


def write(out: Path, peers, published, verdicts: dict[str, str], store: dict[str, bytes],
          config: dict | None = None) -> None:  # fmt: skip
    (out / "content").mkdir(parents=True, exist_ok=True)

    def raw(p) -> str:
        return p.public_key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    info = {
        "provenance": provenance(config or {}),
        "peers": [{"id": p.id, "addr": p.addr, "public_key": raw(p)} for p in peers],
    }
    (out / "peers.json").write_text(json.dumps(info, indent=1) + "\n")
    with (out / "records.jsonl").open("w", encoding="utf-8") as f:
        for rec, forged in published:
            row = {**asdict(rec), "audit": verdicts.get(rec.id), "forged": forged}
            f.write(json.dumps(row) + "\n")
            (out / "content" / rec.hash).write_bytes(store[rec.hash])


def check_record(row: dict, keys: dict[str, Ed25519PublicKey], prev: dict[str, str],
                 content: bytes | None) -> str:  # fmt: skip
    """Why a record cannot be trusted, or "ok". `prev` holds each peer's last record ID so far."""
    rec = Record(**{k: row[k] for k in RECORD_FIELDS if k in row})
    key = keys.get(rec.peer_id)
    if key is None:
        return "unknown-peer"
    if rec.compute_id() != rec.id:
        return "bad-id"
    try:
        key.verify(bytes.fromhex(rec.sig), bytes.fromhex(rec.id))
    except (InvalidSignature, ValueError):
        return "bad-signature"
    if rec.prev != prev.get(rec.peer_id, GENESIS):
        return "broken-chain"
    if content is None:
        return "no-content"
    if content_hash(content) != rec.hash:
        return "content-mismatch"
    return "ok"


def accepted(snapshot: Path, policy: str = "pass") -> tuple[list[tuple[dict, bytes]], dict]:
    """The records whose content may be indexed under `policy`, and a count of every outcome."""
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    keys: dict[str, Ed25519PublicKey] = {}
    for p in json.loads((snapshot / "peers.json").read_text())["peers"]:
        pk = bytes.fromhex(p["public_key"])
        if sha256_hex(pk + p["addr"].encode()) == p["id"]:  # node ID = hash(public key + address)
            keys[p["id"]] = Ed25519PublicKey.from_public_bytes(pk)
    prev: dict[str, str] = {}
    outcomes: Counter[str] = Counter()
    out: list[tuple[dict, bytes]] = []
    forged_admitted = 0
    with (snapshot / "records.jsonl").open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            path = snapshot / "content" / str(row.get("hash", ""))
            content = path.read_bytes() if path.is_file() else None
            reason = check_record(row, keys, prev, content)
            if reason not in ("unknown-peer", "bad-id", "bad-signature", "broken-chain"):
                prev[row["peer_id"]] = row["id"]  # the record is on its peer's chain
            if reason == "ok":
                verdict = row.get("audit")
                if verdict == FAIL:
                    reason = "audit-fail"
                elif policy == "pass" and verdict != PASS:
                    reason = "not-audited" if verdict is None else "audit-inconclusive"
            outcomes[reason] += 1
            if reason == "ok":
                out.append((row, content))
                forged_admitted += bool(row.get("forged"))
    stats = {"policy": policy, "records": sum(outcomes.values()), "accepted": len(out),
             "outcomes": dict(sorted(outcomes.items())),
             "forged_accepted": forged_admitted}  # fmt: skip
    return out, stats


def run(a, out: Path) -> dict:
    """Crawl with simulated peers, audit a sample of the records, and write the snapshot."""
    rng = random.Random(a.seed)
    n_bad = round(a.peers * a.dishonest)
    peers = [Peer(f"10.0.0.{i}:7000", forge_rate=a.forge_rate if i < n_bad else 0.0,
                  rng=random.Random(a.seed * 1000 + i), key_seed=f"sim:{a.seed}:{i}".encode())
             for i in range(a.peers)]  # fmt: skip
    ring, directory = Ring(peers), Directory(peers)
    fetcher = OfflineFetcher() if a.offline else Fetcher(delay=a.delay)
    log = (lambda *_: None) if a.quiet else print
    store: dict[str, bytes] = {}
    published, _ = crawl(load_seeds(Path(a.packages), a.limit), ring, fetcher, log, store)
    assert all(verify(rec, directory) for rec, _ in published)
    if a.wait:
        time.sleep(a.wait)
    verdicts = {rec.id: audit(rec, directory, fetcher)
                for rec, _ in published if rng.random() < a.audit_rate}  # fmt: skip
    write(out, peers, published, verdicts, store, vars(a))
    return {"records": len(published), "forged": sum(f for _, f in published),
            "audited": len(verdicts), "verdicts": dict(Counter(verdicts.values())),
            "requests": fetcher.requests_made}  # fmt: skip


def build_parser():
    ap = sim_parser()
    ap.prog = "python -m crawl_ledger.snapshot"
    ap.description = __doc__
    for action in ap._actions:
        if action.dest == "out":
            action.default = "data/project/ledger"
            action.help = "snapshot directory to write"
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    summary = run(a, Path(a.out))
    for policy in POLICIES:
        summary[policy] = accepted(Path(a.out), policy)[1]
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
