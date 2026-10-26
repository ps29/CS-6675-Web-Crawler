import pytest

from crawl_ledger.merkle import root_from_branch
from edge_serving.gateway import Gateway
from edge_serving.ledger_view import AUDIT_FAIL, AUDIT_PASS, build_demo_ledger
from edge_serving.objects import IMMUTABLE, INCLUDED, PASS, PENDING, REVOKED, Proof, Status


@pytest.fixture
def ledger():
    return build_demo_ledger(["requests", "numpy"], epochs=2)


def test_finality_sets_latest_pointer_to_newest_record(ledger):
    rid = ledger.latest[("requests", "pypi")]
    assert ledger.records[rid].url.endswith("/requests/1")
    assert ledger.state[rid] == INCLUDED


def test_proof_of_final_record_is_immutable_and_verifies(ledger):
    gw = Gateway(ledger)
    rid = ledger.latest[("numpy", "rtd")]
    resp = gw.get(f"/proof/{rid}")
    assert resp.status == 200 and resp.cache_control == IMMUTABLE
    proof = Proof.from_dict(resp.body)
    assert root_from_branch(proof.record.id, proof.branch) == proof.header.root


def test_proof_of_pending_record_is_not_stored(ledger):
    owner = ledger.ring.owner("requests")
    rec, _ = owner.make_record("requests", "pypi", "https://x/requests/9", b"new", now=9e9)
    ledger.submit(rec)
    ledger.seal_epoch()
    resp = Gateway(ledger).get(f"/proof/{rec.id}")
    assert resp.status == 202 and resp.body["state"] == PENDING
    assert resp.cache_control == "no-store"


def test_status_is_signed_and_ttl_cached(ledger):
    gw = Gateway(ledger, t_status=600)
    rid = ledger.latest[("numpy", "pypi")]
    resp = gw.get(f"/status/{rid}")
    assert resp.cache_control == "s-maxage=600"
    status = Status.from_dict(resp.body)
    gw.public_key.verify(bytes.fromhex(status.sig), status.signed_bytes())
    assert status.state == INCLUDED and status.epoch == ledger.head_epoch()


def test_audits_change_status_and_queue_delta(ledger):
    passed = ledger.latest[("numpy", "pypi")]
    failed = ledger.latest[("requests", "rtd")]
    ledger.record_audit(passed, AUDIT_PASS)
    ledger.record_audit(failed, AUDIT_FAIL)
    assert ledger.state[passed] == PASS and ledger.state[failed] == REVOKED
    assert (failed, REVOKED) in ledger.pending_changes().status_changes
    hdr = ledger.seal_epoch()
    assert (failed, REVOKED) in ledger.sealed_changes[hdr.epoch].status_changes


def test_unknown_record_and_package(ledger):
    gw = Gateway(ledger)
    missing = gw.get("/proof/" + "ab" * 32)
    assert missing.status == 404 and missing.cache_control == "no-store"
    assert gw.get("/latest/nope/pypi").status == 404
    assert gw.get("/bogus").status == 400


def test_cannot_audit_pending_record(ledger):
    owner = ledger.ring.owner("numpy")
    rec, _ = owner.make_record("numpy", "pypi", "https://x/numpy/9", b"new", now=9e9)
    ledger.submit(rec)
    with pytest.raises(ValueError):
        ledger.record_audit(rec.id, AUDIT_PASS)
