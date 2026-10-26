import asyncio
import socket

from edge_serving.client import ACCEPT
from edge_serving.http_app import demo


def free_port_block(n: int) -> int:
    """A base port whose next n ports are free on localhost."""
    for _ in range(50):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            base = s.getsockname()[1]
        if base + n < 65535 and all(_free(base + i) for i in range(n + 1)):
            return base
    raise RuntimeError("no free port block")


def _free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def test_demo_serves_verifiable_record_through_edge():
    result = asyncio.run(demo(free_port_block(3), n_edges=3))
    assert result["verdict"] == ACCEPT
    # 5 concurrent requests: pointer, proof and status fetched upstream once each
    assert result["gateway_requests"] == 3
    # each of the other 4 was either coalesced or served as 3 cache hits (pointer/proof/status)
    assert result["coalesced"] + result["edge_hits"] // 3 == 4
