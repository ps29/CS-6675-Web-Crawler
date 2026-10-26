import dataclasses

import pytest

from crawl_ledger.blocks import HeaderChain, build_header
from crawl_ledger.record import sha256_hex
from edge_serving.client import ACCEPT, IDE_POLICY, RAG_POLICY, REFETCH, REJECT
from edge_serving.delta import Delta
from edge_serving.gateway import Gateway
from edge_serving.ledger_view import AUDIT_FAIL, AUDIT_PASS, build_demo_ledger
from edge_serving.objects import REVOKED
from edge_serving.revocation import R1Client, R1Edge
from tests.edge.helpers import Clock, consumer_chain


@pytest.fixture
def env():
    ledger = build_demo_ledger(["requests", "numpy"], epochs=2)
    gateway = Gateway(ledger)
    edge = R1Edge(
        "tokyo", gateway.get, Clock(), HeaderChain(ledger.directory, ledger.chain.final_depth)
    )
    head = ledger.headers[-1]
    assert edge.on_header(head, ledger.deltas[head.epoch], ledger.header_and_delta)
    return ledger, gateway, edge


def gossip(ledger, edge):
    hdr = ledger.seal_epoch()
    return edge.on_header(hdr, ledger.deltas[hdr.epoch], ledger.header_and_delta)


def client(ledger, gateway, policy=IDE_POLICY):
    return R1Client(consumer_chain(ledger), ledger.directory, gateway.public_key, policy)


def test_m3_header_ids_unchanged_without_delta_root():
    ledger = build_demo_ledger(["requests"], epochs=1)
    hdr, _ = build_header(9, [], ledger.peers[0], "0" * 64)
    blob = (
        '{"epoch":9,"prev":"' + "0" * 64 + '","proposer":"' + ledger.peers[0].id + '","root":"'
        + "0" * 64 + '"}'
    )  # fmt: skip
    assert hdr.id == sha256_hex(blob.encode())


def test_edge_bootstraps_from_gap_fetch(env):
    ledger, _, edge = env
    assert edge.synced_epoch == ledger.head_epoch()
    assert ("numpy", "pypi") in edge.pointers


def test_pointer_moves_are_committed_by_the_next_header(env):
    """Finality reached while sealing epoch e is listed in the delta of epoch e + 1."""
    ledger, _, edge = env
    key = ("numpy", "pypi")
    assert edge.pointers[key] != ledger.latest[key]
    assert gossip(ledger, edge)
    assert edge.pointers[key] == ledger.latest[key]


def test_statuses_and_pointers_need_no_gateway_requests(env):
    ledger, gateway, edge = env
    body = edge.get_latest("requests", "pypi").body
    edge.get_latest("requests", "pypi")
    assert gateway.requests == 1  # the proof, once
    assert body["synced_epoch"] == ledger.head_epoch()
    assert client(ledger, gateway).check_latest(body) == ACCEPT


def test_revocation_visible_at_next_header(env):
    ledger, gateway, edge = env
    rid = ledger.latest[("numpy", "rtd")]
    assert client(ledger, gateway).check_latest(edge.get_latest("numpy", "rtd").body) == ACCEPT
    ledger.record_audit(rid, AUDIT_FAIL)
    assert gossip(ledger, edge)
    body = edge.get_latest("numpy", "rtd").body
    assert body["status"]["state"] == REVOKED
    assert client(ledger, gateway).check_latest(body) == REJECT


def test_audit_pass_satisfies_rag_policy(env):
    ledger, gateway, edge = env
    rid = ledger.latest[("requests", "rtd")]
    ledger.record_audit(rid, AUDIT_PASS)
    gossip(ledger, edge)
    body = edge.get_latest("requests", "rtd").body
    assert client(ledger, gateway, RAG_POLICY).check_latest(body) == ACCEPT


def test_dropped_delta_entry_is_detected(env):
    ledger, _, edge = env
    rid = ledger.latest[("numpy", "pydocs")]
    ledger.record_audit(rid, AUDIT_FAIL)
    hdr = ledger.seal_epoch()
    withheld = dataclasses.replace(ledger.deltas[hdr.epoch], status_changes=())
    before = edge.synced_epoch
    assert not edge.on_header(hdr, withheld, ledger.header_and_delta)
    assert edge.synced_epoch == before and edge.rejected_deltas == 1


def test_failed_gap_fetch_falls_back_to_ttl(env):
    ledger, gateway, edge = env
    ledger.seal_epoch()
    hdr = ledger.seal_epoch()
    assert not edge.on_header(hdr, ledger.deltas[hdr.epoch], lambda epoch: None)
    assert edge.ttl_fallback
    before = gateway.requests
    edge.get_latest("requests", "pypi")
    assert gateway.requests > before  # pointer and status now come from the gateway


def test_forged_status_entry_is_rejected(env):
    ledger, gateway, edge = env
    ledger.record_audit(ledger.latest[("numpy", "rtd")], AUDIT_FAIL)
    gossip(ledger, edge)
    body = edge.get_latest("numpy", "rtd").body
    body["status"] = {**body["status"], "state": "PASS"}
    assert client(ledger, gateway).check_latest(body) == REJECT


def test_implied_included_requires_final_block(env):
    ledger, gateway, edge = env
    body = edge.get_latest("requests", "pypi").body
    body["status"] = {**body["status"], "state": "PASS", "implied": True}
    assert client(ledger, gateway).check_latest(body) == REJECT


def test_lagging_edge_needs_refetch(env):
    ledger, gateway, edge = env
    body = edge.get_latest("requests", "pypi").body
    for _ in range(IDE_POLICY.max_lag + 1):
        ledger.seal_epoch()
    assert client(ledger, gateway).check_latest(body) == REFETCH


def test_delta_root_of_empty_delta_is_stable():
    assert Delta(3).root() == Delta(3).root() == "0" * 64
