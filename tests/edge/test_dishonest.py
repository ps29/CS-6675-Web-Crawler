import pytest

from crawl_ledger.blocks import HeaderChain
from edge_serving.client import ACCEPT, IDE_POLICY, RAG_POLICY, REFETCH, REJECT, Client
from edge_serving.dishonest import FREEZE, WITHHOLD, LyingR1Edge, ReplayEdge
from edge_serving.gateway import Gateway
from edge_serving.ledger_view import AUDIT_FAIL, AUDIT_PASS, build_demo_ledger
from edge_serving.revocation import R1Client, R1Edge
from edge_serving.sim import build_parser, run_variant
from tests.edge.helpers import Clock, consumer_chain

KEY = ("requests", "pypi")


def r1_env(edge_class=R1Edge, **kwargs):
    ledger = build_demo_ledger(["requests", "numpy"], epochs=2)
    gateway = Gateway(ledger)
    chain = HeaderChain(ledger.directory, ledger.chain.final_depth)
    edge = edge_class("tokyo", gateway.get, Clock(), chain, **kwargs)
    head = ledger.headers[-1]
    assert edge.on_header(head, ledger.deltas[head.epoch], ledger.header_and_delta)
    gossip(ledger, edge)  # commits the pointers that became final while building the ledger
    edge.active = True
    return ledger, gateway, edge


def gossip(ledger, edge):
    hdr = ledger.seal_epoch()
    return edge.on_header(hdr, ledger.deltas[hdr.epoch], ledger.header_and_delta)


def r1_verdict(ledger, gateway, edge, policy=IDE_POLICY):
    body = edge.get_latest(*KEY).body
    client = R1Client(consumer_chain(ledger), ledger.directory, gateway.public_key, policy)
    return client.check_latest(body), body


def revoke_latest(ledger, edge):
    ledger.record_audit(ledger.latest[KEY], AUDIT_FAIL)
    gossip(ledger, edge)


def test_honest_r1_edge_serves_the_revocation():
    ledger, gateway, edge = r1_env()
    revoke_latest(ledger, edge)
    assert r1_verdict(ledger, gateway, edge)[0] == REJECT


def test_frozen_edge_that_admits_its_lag_is_refetched():
    ledger, gateway, edge = r1_env()
    edge.on_header = lambda *args: True  # stops following gossip, reports its real epoch
    revoke_latest(ledger, edge)
    for _ in range(IDE_POLICY.max_lag + 1):
        gossip(ledger, edge)
    assert r1_verdict(ledger, gateway, edge)[0] == REFETCH


def test_freeze_with_misreported_epoch_hides_a_revocation():
    ledger, gateway, edge = r1_env(LyingR1Edge, attack=FREEZE)
    real_epoch = edge.synced_epoch
    revoke_latest(ledger, edge)
    for _ in range(IDE_POLICY.max_lag + 1):
        gossip(ledger, edge)
    verdict, body = r1_verdict(ledger, gateway, edge)
    assert verdict == ACCEPT
    assert edge.synced_epoch == real_epoch
    assert body["synced_epoch"] == ledger.head_epoch()


def test_withholding_hides_a_revocation_with_a_truthful_epoch():
    ledger, gateway, edge = r1_env(LyingR1Edge, attack=WITHHOLD)
    revoke_latest(ledger, edge)
    for _ in range(IDE_POLICY.max_lag + 1):
        gossip(ledger, edge)
    verdict, body = r1_verdict(ledger, gateway, edge)
    assert verdict == ACCEPT
    assert edge.synced_epoch == ledger.head_epoch() == body["synced_epoch"]


def test_withholding_replays_an_old_pass_past_a_strict_policy():
    ledger, gateway, edge = r1_env(LyingR1Edge, attack=WITHHOLD)
    ledger.record_audit(ledger.latest[KEY], AUDIT_PASS)
    gossip(ledger, edge)
    revoke_latest(ledger, edge)
    for _ in range(RAG_POLICY.max_lag + 1):
        gossip(ledger, edge)
    assert r1_verdict(ledger, gateway, edge, RAG_POLICY)[0] == ACCEPT


def test_withholding_cannot_upgrade_a_record_that_never_passed():
    ledger, gateway, edge = r1_env(LyingR1Edge, attack=WITHHOLD)
    revoke_latest(ledger, edge)
    assert r1_verdict(ledger, gateway, edge, RAG_POLICY)[0] == REJECT


@pytest.fixture
def replay_env():
    ledger = build_demo_ledger(["requests", "numpy"], epochs=2)
    gateway = Gateway(ledger, t_status=0)
    edge = ReplayEdge("tokyo", gateway.get, Clock())
    client = Client(consumer_chain(ledger), ledger.directory, gateway.public_key, IDE_POLICY)
    assert client.check_latest(edge.get_latest(*KEY).body) == ACCEPT
    ledger.record_audit(ledger.latest[KEY], AUDIT_FAIL)
    return ledger, gateway, edge


def test_baseline_replay_is_accepted_within_max_lag(replay_env):
    ledger, gateway, edge = replay_env
    ledger.seal_epoch()
    client = Client(consumer_chain(ledger), ledger.directory, gateway.public_key, IDE_POLICY)
    assert client.check_latest(edge.get_latest(*KEY).body) == ACCEPT


def test_baseline_replay_is_bounded_by_max_lag(replay_env):
    ledger, gateway, edge = replay_env
    for _ in range(IDE_POLICY.max_lag + 1):
        ledger.seal_epoch()
    client = Client(consumer_chain(ledger), ledger.directory, gateway.public_key, IDE_POLICY)
    assert client.check_latest(edge.get_latest(*KEY).body) == REFETCH


SMALL = [
    "--packages", "10", "--peers", "8", "--requests", "600", "--epochs", "8",
    "--revoke-rate", "0.1", "--seed", "1",
]  # fmt: skip


def simulate(variant, *extra):
    return run_variant(variant, build_parser().parse_args([*SMALL, *extra]), t_status=60)


def test_sim_honest_edges_keep_r1_window_within_gossip_delay():
    assert simulate("r1")["stale_window_s"] <= 5.0


@pytest.mark.parametrize("attack", [WITHHOLD, FREEZE])
@pytest.mark.parametrize("variant", ["r1", "both"])
def test_sim_dishonest_r1_edge_outlasts_max_lag(variant, attack):
    bound = (IDE_POLICY.max_lag + 1) * 600
    r = simulate(variant, "--dishonest-edges", "3", "--attack", attack)
    assert r["stale_accepts"] > 0 and r["stale_window_s"] > bound


def test_sim_dishonest_baseline_edge_stays_within_max_lag():
    bound = (IDE_POLICY.max_lag + 1) * 600
    r = simulate("baseline", "--dishonest-edges", "3")
    assert 0 < r["stale_window_s"] <= bound
