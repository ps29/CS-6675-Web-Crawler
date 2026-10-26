import dataclasses

import pytest

from edge_serving.client import ACCEPT, IDE_POLICY, RAG_POLICY, REFETCH, REJECT, Client
from edge_serving.edge import Edge
from edge_serving.gateway import Gateway
from edge_serving.ledger_view import AUDIT_FAIL, AUDIT_PASS, build_demo_ledger
from edge_serving.objects import Proof, Status
from tests.edge.helpers import Clock, consumer_chain

T_PTR, T_STATUS = 60, 600


@pytest.fixture
def env():
    ledger = build_demo_ledger(["requests", "numpy", "flask"], epochs=2)
    gateway = Gateway(ledger, t_ptr=T_PTR, t_status=T_STATUS)
    clock = Clock()
    edge = Edge("london", gateway.get, clock)
    return ledger, gateway, clock, edge


def client_for(ledger, gateway, policy=IDE_POLICY):
    return Client(consumer_chain(ledger), ledger.directory, gateway.public_key, policy)


def test_proofs_are_cached_until_evicted(env):
    ledger, gateway, _, edge = env
    rid = ledger.latest[("requests", "pypi")]
    edge.get(f"/proof/{rid}")
    edge.get(f"/proof/{rid}")
    assert gateway.requests == 1 and edge.stats.hits == 1


def test_status_and_pointer_expire_on_ttl(env):
    ledger, gateway, clock, edge = env
    rid = ledger.latest[("numpy", "pypi")]
    edge.get(f"/status/{rid}")
    edge.get("/latest/numpy/pypi")
    clock.advance(T_PTR + 1)
    edge.get(f"/status/{rid}")  # still fresh
    edge.get("/latest/numpy/pypi")  # expired
    assert gateway.requests == 3
    clock.advance(T_STATUS)
    edge.get(f"/status/{rid}")
    assert gateway.requests == 4


def test_pending_and_errors_are_not_cached(env):
    ledger, gateway, _, edge = env
    owner = ledger.ring.owner("flask")
    rec, _ = owner.make_record("flask", "pypi", "https://x/flask/9", b"new", now=9e9)
    ledger.submit(rec)
    ledger.seal_epoch()
    edge.get(f"/proof/{rec.id}")
    edge.get(f"/proof/{rec.id}")
    edge.get("/latest/unknown/pypi")
    edge.get("/latest/unknown/pypi")
    assert gateway.requests == 4


def test_lru_evicts_oldest_proof(env):
    ledger, gateway, clock, _ = env
    edge = Edge("tiny", gateway.get, clock, proof_capacity=1)
    a, b = ledger.latest[("requests", "pypi")], ledger.latest[("numpy", "pypi")]
    for rid in (a, b, a):
        edge.get(f"/proof/{rid}")
    assert gateway.requests == 3 and list(edge.proofs) == [f"/proof/{a}"]


def test_get_latest_is_accepted_by_client(env):
    ledger, gateway, _, edge = env
    resp = edge.get_latest("requests", "rtd")
    assert resp.status == 200
    assert client_for(ledger, gateway).check_latest(resp.body) == ACCEPT


def test_rag_policy_needs_audit_pass(env):
    ledger, gateway, clock, edge = env
    rag = client_for(ledger, gateway, RAG_POLICY)
    assert rag.check_latest(edge.get_latest("flask", "pydocs").body) == REJECT
    ledger.record_audit(ledger.latest[("flask", "pydocs")], AUDIT_PASS)
    clock.advance(T_STATUS + 1)
    assert rag.check_latest(edge.get_latest("flask", "pydocs").body) == ACCEPT


def test_baseline_serves_revoked_record_until_status_ttl(env):
    """The weakness R1 fixes: an edge keeps vouching for a revoked record."""
    ledger, gateway, clock, edge = env
    client = client_for(ledger, gateway)
    edge.get_latest("numpy", "rtd")
    ledger.record_audit(ledger.latest[("numpy", "rtd")], AUDIT_FAIL)
    clock.advance(T_STATUS - 1)
    assert client.check_latest(edge.get_latest("numpy", "rtd").body) == ACCEPT
    clock.advance(2)
    assert client.check_latest(edge.get_latest("numpy", "rtd").body) == REJECT


def test_tampered_proof_and_status_are_rejected(env):
    ledger, gateway, _, edge = env
    client = client_for(ledger, gateway)
    body = edge.get_latest("requests", "pypi").body
    proof, status = Proof.from_dict(body["proof"]), Status.from_dict(body["status"])

    forged_rec = dataclasses.replace(proof.record, hash="00" * 32)
    assert client.check(dataclasses.replace(proof, record=forged_rec), status) == REJECT
    assert client.check(proof, dataclasses.replace(status, state="PASS")) == REJECT
    assert client.check(proof, dataclasses.replace(status, rid="ab" * 32)) == REJECT
    assert client.check_latest({"proof": body["proof"]}) == REJECT


def test_old_status_beyond_max_lag_needs_refetch(env):
    ledger, gateway, _, edge = env
    body = edge.get_latest("requests", "pypi").body
    for _ in range(IDE_POLICY.max_lag + 1):
        ledger.seal_epoch()
    assert client_for(ledger, gateway).check_latest(body) == REFETCH
