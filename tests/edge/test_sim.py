import pytest

from edge_serving.sim import build_parser, run_variant

SMALL = [
    "--packages", "10", "--peers", "8", "--requests", "600", "--epochs", "4",
    "--revoke-rate", "0.1", "--seed", "1",
]  # fmt: skip


@pytest.fixture(scope="module")
def results():
    a = build_parser().parse_args(SMALL)
    return {
        "baseline_long": run_variant("baseline", a, t_status=3600),
        "r1": run_variant("r1", a, t_status=60),
        "both": run_variant("both", a, t_status=60),
    }


def test_r1_bounds_stale_window_by_gossip_delay(results):
    assert results["r1"]["stale_window_s"] <= 5.0
    assert results["baseline_long"]["stale_window_s"] > results["r1"]["stale_window_s"]


def test_r1_reduces_gateway_load(results):
    assert results["r1"]["gateway_rps"] < results["baseline_long"]["gateway_rps"]
    assert results["r1"]["gossip_bytes_per_edge_epoch"] > 0


def test_r2_shares_proofs(results):
    assert results["both"]["sibling_hits"] > 0
    assert results["both"]["gateway_requests"] <= results["r1"]["gateway_requests"]


def test_every_request_is_counted(results):
    assert all(r["requests"] == 600 for r in results.values())
