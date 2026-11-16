"""Ledger snapshots: what a reader accepts for indexing, and what it rejects after tampering."""

import json
from collections import Counter

import pytest

from crawl_ledger.snapshot import accepted, build_parser, run


def snapshot(tmp_path, *extra):
    args = build_parser().parse_args(
        ["--offline", "--quiet", "--dishonest", "0.5", "--forge-rate", "0.5", "--audit-rate",
         "0.5", *extra]
    )  # fmt: skip
    made = run(args, tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "records.jsonl").read_text().splitlines()]
    return made, rows


def rewrite(tmp_path, rows):
    (tmp_path / "records.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_pass_policy_admits_only_audited_pass_records(tmp_path):
    made, rows = snapshot(tmp_path)
    assert made["forged"] > 0 and 0 < made["audited"] < made["records"]
    got, stats = accepted(tmp_path, "pass")
    assert all(row["audit"] == "PASS" and not row["forged"] for row, _ in got)
    assert stats["accepted"] == made["verdicts"]["PASS"] and stats["forged_accepted"] == 0
    assert stats["outcomes"]["not-audited"] == made["records"] - made["audited"]


def test_not_failed_policy_admits_unaudited_records_including_forged_ones(tmp_path):
    made, rows = snapshot(tmp_path)
    _, stats = accepted(tmp_path, "not-failed")
    assert stats["accepted"] == made["records"] - made["verdicts"]["FAIL"]
    unaudited_forged = sum(r["forged"] and r["audit"] is None for r in rows)
    assert stats["forged_accepted"] == unaudited_forged > 0


def test_full_audit_keeps_every_forged_record_out_under_both_policies(tmp_path):
    made, _ = snapshot(tmp_path, "--audit-rate", "1.0")
    for policy in ("pass", "not-failed"):
        _, stats = accepted(tmp_path, policy)
        assert stats["forged_accepted"] == 0
        assert stats["accepted"] == made["records"] - made["forged"]


def test_changed_content_is_rejected(tmp_path):
    _, rows = snapshot(tmp_path, "--audit-rate", "1.0", "--dishonest", "0")
    victim = rows[3]
    (tmp_path / "content" / victim["hash"]).write_bytes(b"something else entirely")
    got, stats = accepted(tmp_path, "pass")
    assert stats["outcomes"]["content-mismatch"] == 1
    assert victim["id"] not in {row["id"] for row, _ in got}


def test_edited_record_and_bad_signature_are_rejected(tmp_path):
    _, rows = snapshot(tmp_path, "--audit-rate", "1.0", "--dishonest", "0")
    # the last record of each of two peers, so that no later record depends on them
    last = {r["peer_id"]: i for i, r in enumerate(rows)}
    a, b = list(last.values())[:2]
    rows[a]["url"] = "https://evil.example/"
    rows[b]["sig"] = "00" * 64
    rewrite(tmp_path, rows)
    _, stats = accepted(tmp_path, "pass")
    assert stats["outcomes"]["bad-id"] == 1 and stats["outcomes"]["bad-signature"] == 1
    assert stats["accepted"] == len(rows) - 2


def test_removing_a_record_breaks_the_rest_of_that_peers_chain(tmp_path):
    _, rows = snapshot(tmp_path, "--audit-rate", "1.0", "--dishonest", "0")
    peer, count = Counter(r["peer_id"] for r in rows).most_common(1)[0]
    assert count >= 3
    first = next(i for i, r in enumerate(rows) if r["peer_id"] == peer)
    rewrite(tmp_path, rows[:first] + rows[first + 1 :])
    _, stats = accepted(tmp_path, "pass")
    assert stats["outcomes"]["broken-chain"] == count - 1


def test_a_peer_with_a_swapped_key_is_unknown(tmp_path):
    _, rows = snapshot(tmp_path, "--audit-rate", "1.0", "--dishonest", "0")
    info = json.loads((tmp_path / "peers.json").read_text())
    peer = rows[0]["peer_id"]
    other = next(p for p in info["peers"] if p["id"] != peer)
    for p in info["peers"]:
        if p["id"] == peer:
            p["public_key"] = other["public_key"]  # no longer hashes to the node ID
    (tmp_path / "peers.json").write_text(json.dumps(info))
    _, stats = accepted(tmp_path, "pass")
    assert stats["outcomes"]["unknown-peer"] == sum(r["peer_id"] == peer for r in rows)


def test_unknown_policy_is_refused(tmp_path):
    snapshot(tmp_path)
    with pytest.raises(ValueError):
        accepted(tmp_path, "anything")
