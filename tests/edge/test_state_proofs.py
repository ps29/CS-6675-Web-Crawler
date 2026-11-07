"""R1-S: proofs of non-revocation through a header state_root."""

import pytest

from crawl_ledger.blocks import HeaderChain, build_header
from crawl_ledger.record import sha256_hex
from edge_serving.client import ACCEPT, IDE_POLICY, REFETCH, REJECT
from edge_serving.delta import StateTree
from edge_serving.dishonest import (
    FREEZE,
    WITHHOLD,
    LyingStateProofEdge,
    ReplayStateProofEdge,
)
from edge_serving.gateway import Gateway
from edge_serving.ledger_view import AUDIT_FAIL, AUDIT_PASS, build_demo_ledger
from edge_serving.objects import INCLUDED, PASS, PENDING, REVOKED
from edge_serving.sim import build_parser, run_variant
from edge_serving.state_proofs import StateProofClient, StateProofEdge
from tests.edge.helpers import Clock, consumer_chain

KEY = ("requests", "pypi")


def env(edge_class=StateProofEdge, **kwargs):
    ledger = build_demo_ledger(["requests", "numpy"], epochs=2, state_proofs=True)
    gateway = Gateway(ledger)
    chain = HeaderChain(ledger.directory, ledger.chain.final_depth)
    edge = edge_class("tokyo", gateway.get, Clock(), chain, **kwargs)
    head = ledger.headers[-1]
    assert edge.on_header(head, ledger.deltas[head.epoch], ledger.header_and_delta)
    gossip(ledger, edge)  # commits the pointers and states that became final while building
    edge.active = True
    return ledger, gateway, edge


def gossip(ledger, edge):
    hdr = ledger.seal_epoch()
    return edge.on_header(hdr, ledger.deltas[hdr.epoch], ledger.header_and_delta)


def verdict(ledger, gateway, edge, policy=IDE_POLICY):
    body = edge.get_latest(*KEY).body
    client = StateProofClient(consumer_chain(ledger), ledger.directory, gateway.public_key, policy)
    return client.check_latest(body), body


def test_header_ids_unchanged_without_state_root():
    ledger = build_demo_ledger(["requests"], epochs=1)
    hdr, _ = build_header(9, [], ledger.peers[0], "0" * 64)
    blob = (
        '{"epoch":9,"prev":"' + "0" * 64 + '","proposer":"' + ledger.peers[0].id + '","root":"'
        + "0" * 64 + '"}'
    )  # fmt: skip
    assert hdr.id == sha256_hex(blob.encode())
    assert all(not h.state_root for h in ledger.headers)


def test_state_root_enters_the_header_id():
    ledger = build_demo_ledger(["requests"], epochs=1, state_proofs=True)
    hdr = ledger.headers[-1]
    assert hdr.state_root
    original = hdr.compute_id()
    hdr.state_root = "f" * 64
    assert hdr.compute_id() != original


def test_state_tree_branches_verify_and_exclude_pending():
    from crawl_ledger.merkle import root_from_branch
    from edge_serving.delta import leaf_hash, state_leaf

    tree = StateTree({"aa": INCLUDED, "bb": PASS, "cc": REVOKED})
    for rid, state in (("aa", INCLUDED), ("bb", PASS), ("cc", REVOKED)):
        branch = tree.branch(rid)
        assert root_from_branch(leaf_hash(state_leaf(rid, state)), branch) == tree.root()
    assert tree.branch("dd") is None


def test_edge_rebuilds_the_ledger_state_from_deltas():
    ledger, _, edge = env()
    assert edge.tree().root() == ledger.headers[-1].state_root
    assert edge.root_mismatches == 0
    assert all(edge.state[r] == s for r, s in ledger.state.items() if s != PENDING)


def test_honest_edge_accepts_then_rejects_after_revocation():
    ledger, gateway, edge = env()
    assert verdict(ledger, gateway, edge)[0] == ACCEPT
    ledger.record_audit(ledger.latest[KEY], AUDIT_FAIL)
    gossip(ledger, edge)
    result, body = verdict(ledger, gateway, edge)
    assert body["status"]["state"] == REVOKED
    assert result == REJECT


def test_pass_is_proven_by_the_state_branch():
    ledger, gateway, edge = env()
    ledger.record_audit(ledger.latest[KEY], AUDIT_PASS)
    gossip(ledger, edge)
    result, body = verdict(ledger, gateway, edge)
    assert body["status"]["state"] == PASS and result == ACCEPT


@pytest.mark.parametrize("field,value", [("state", PASS), ("epoch", 0), ("state_branch", [])])
def test_tampered_status_is_rejected(field, value):
    ledger, gateway, edge = env()
    body = edge.get_latest(*KEY).body
    body["status"][field] = value
    client = StateProofClient(
        consumer_chain(ledger), ledger.directory, gateway.public_key, IDE_POLICY
    )
    assert client.check_latest(body) in (REJECT, REFETCH)
    assert client.check_latest(body) != ACCEPT


def test_withholding_edge_fails_safe():
    """Keeping a revoked record out of its state makes the edge's tree disagree with the header,
    so every branch it serves for that epoch is rejected instead of accepted."""
    ledger, gateway, edge = env(LyingStateProofEdge, attack=WITHHOLD)
    ledger.record_audit(ledger.latest[KEY], AUDIT_FAIL)
    gossip(ledger, edge)
    result, body = verdict(ledger, gateway, edge)
    assert body["status"]["state"] != REVOKED
    assert result == REJECT
    assert edge.root_mismatches >= 1


@pytest.mark.parametrize("edge_class,kwargs", [
    (ReplayStateProofEdge, {}),
    (LyingStateProofEdge, {"attack": FREEZE}),
])  # fmt: skip
def test_replayed_state_is_bounded_by_max_lag(edge_class, kwargs):
    ledger, gateway, edge = env(edge_class, **kwargs)
    verdict(ledger, gateway, edge)  # the replay edge remembers this pre-revocation status
    ledger.record_audit(ledger.latest[KEY], AUDIT_FAIL)
    gossip(ledger, edge)
    for _ in range(IDE_POLICY.max_lag - 1):
        gossip(ledger, edge)
    assert verdict(ledger, gateway, edge)[0] == ACCEPT  # still within max_lag: the known cost
    gossip(ledger, edge)
    assert verdict(ledger, gateway, edge)[0] == REFETCH


def test_sim_r1s_matches_r1_when_honest():
    a = build_parser().parse_args(["--requests", "2000", "--epochs", "4"])
    r1 = run_variant("r1", a, 60.0)
    r1s = run_variant("r1s", a, 60.0)
    assert r1s["stale_accepts"] <= r1["stale_accepts"] + 1
    assert r1s["gateway_requests"] == r1["gateway_requests"]


def test_sim_r1s_bounds_a_replaying_edge():
    args = ["--requests", "4000", "--epochs", "8", "--dishonest-edges", "1", "--attack", "replay"]
    a = build_parser().parse_args(args)
    r = run_variant("r1s", a, 60.0)
    bound = (IDE_POLICY.max_lag + 1) * a.epoch_seconds
    assert r["stale_window_s"] <= bound


def test_spot_checks_block_a_lying_edge_and_spare_honest_ones():
    args = ["--requests", "8000", "--epochs", "8", "--dishonest-edges", "1", "--spot-check", "0.2"]
    a = build_parser().parse_args(args)
    r = run_variant("r1", a, 60.0)
    assert r["spot_checks"] > 0
    assert "edge0" in r["blocked_edges"]
    assert r["honest_edges_blocked"] == 0
