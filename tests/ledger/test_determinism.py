"""Seeded simulations are reproducible: same seed, same peer keys, same ownership, same verdicts."""

import contextlib
import io

from crawl_ledger import sim_m3
from crawl_ledger.peer import Peer
from tests.ledger.test_m3 import TestSim

VERDICT_KEYS = ("forged_audited", "detection_rate", "false_rejection_rate")


def test_key_seed_fixes_the_node_id():
    a, b = Peer("10.0.0.1:7000", key_seed=b"x"), Peer("10.0.0.1:7000", key_seed=b"x")
    assert a.id == b.id
    assert a.id != Peer("10.0.0.1:7000", key_seed=b"y").id
    assert Peer("10.0.0.1:7000").id != Peer("10.0.0.1:7000").id  # unseeded keys stay random
    assert a.public_key.public_bytes_raw() == b.public_key.public_bytes_raw()


def run():
    with contextlib.redirect_stdout(io.StringIO()):
        return sim_m3.main(["--compare"] + TestSim.ARGS)


def test_m3_simulation_is_reproducible():
    first, second = run(), run()
    assert [{k: r[k] for k in VERDICT_KEYS} for r in first] == [
        {k: r[k] for k in VERDICT_KEYS} for r in second
    ]
