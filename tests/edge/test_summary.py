import dataclasses

import pytest

from crawl_ledger.blocks import HeaderChain
from crawl_ledger.verify import verify_record_r1
from edge_serving.gateway import Gateway
from edge_serving.ledger_view import build_demo_ledger
from edge_serving.objects import IMMUTABLE, Proof, Response
from edge_serving.summary import BloomFilter, R1R2Edge, R2Edge
from tests.edge.helpers import Clock


def test_bloom_has_no_false_negatives_and_bounded_false_positives():
    bloom = BloomFilter(capacity=1000, fp_rate=0.01)
    items = [f"item-{i}" for i in range(1000)]
    for item in items:
        bloom.add(item)
    assert all(bloom.might_contain(i) for i in items)
    false_pos = sum(bloom.might_contain(f"other-{i}") for i in range(10_000))
    assert false_pos / 10_000 < 0.02


@pytest.fixture
def env():
    ledger = build_demo_ledger(["requests", "numpy"], epochs=1)
    gateway = Gateway(ledger)
    clock = Clock()

    def verify(proof: Proof) -> bool:
        return verify_record_r1(
            proof.record, proof.branch, proof.header, ledger.chain, ledger.directory
        )

    a = R2Edge("paris", gateway.get, clock, verify_proof=verify, summary_capacity=100)
    b = R2Edge("london", gateway.get, clock, verify_proof=verify, summary_capacity=100)
    a.add_sibling(b, rtt=0.01)
    b.add_sibling(a, rtt=0.01)
    return ledger, gateway, a, b


def test_sibling_hit_avoids_gateway(env):
    ledger, gateway, a, b = env
    path = f"/proof/{ledger.latest[('requests', 'pypi')]}"
    b.get(path)
    b.publish_summary()
    a.get(path)
    assert gateway.requests == 1 and a.sibling_hits == 1 and a.inter_edge_bytes > 0
    a.get(path)
    assert a.stats.hits == 1  # stored locally after the sibling fetch


def test_no_summary_match_goes_to_gateway(env):
    ledger, gateway, a, b = env
    b.publish_summary()
    a.get(f"/proof/{ledger.latest[('numpy', 'pypi')]}")
    assert gateway.requests == 1 and a.sibling_hits == 0


def test_stale_summary_counts_as_false_positive(env):
    ledger, gateway, a, b = env
    path = f"/proof/{ledger.latest[('requests', 'rtd')]}"
    b.get(path)
    b.publish_summary()
    b.proofs.clear()
    a.get(path)
    assert a.sibling_false_positives == 1 and gateway.requests == 2


def test_forged_sibling_copy_is_rejected_and_not_cached(env):
    ledger, gateway, a, b = env
    rid = ledger.latest[("numpy", "rtd")]
    path = f"/proof/{rid}"
    b.get(path)
    good = b.proofs[path].response
    proof = Proof.from_dict(good.body)
    forged = dataclasses.replace(proof, record=dataclasses.replace(proof.record, hash="00" * 32))
    b.proofs[path].response = Response(200, forged.to_dict(), IMMUTABLE)
    b.publish_summary()
    resp = a.get(path)
    assert a.sibling_bad_copies == 1
    assert Proof.from_dict(resp.body).record.hash == proof.record.hash
    assert gateway.requests == 2


def test_statuses_never_come_from_siblings(env):
    ledger, gateway, a, b = env
    path = f"/status/{ledger.latest[('requests', 'pypi')]}"
    b.get(path)
    b.publish_summary()
    a.get(path)
    assert gateway.requests == 2 and a.inter_edge_bytes == 0


def test_combines_with_r1_edge():
    ledger = build_demo_ledger(["requests"], epochs=1)
    gateway = Gateway(ledger)
    edge = R1R2Edge(
        "x",
        gateway.get,
        Clock(),
        HeaderChain(ledger.directory, ledger.chain.final_depth),
        verify_proof=lambda p: True,
    )
    hdr = ledger.seal_epoch()  # commits the pointers that became final in the last epoch
    assert edge.on_header(hdr, ledger.deltas[hdr.epoch], ledger.header_and_delta)
    resp = edge.get_latest("requests", "pypi")
    assert resp.status == 200 and resp.body["synced_epoch"] == hdr.epoch
