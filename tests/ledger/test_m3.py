import random
import unittest

from crawl_ledger import sim_m3
from crawl_ledger.audit import FAIL, INCONCLUSIVE, PASS
from crawl_ledger.blocks import HeaderChain, build_header
from crawl_ledger.config import Config
from crawl_ledger.merkle import merkle_branch, merkle_root, root_from_branch
from crawl_ledger.peer import Directory, Peer
from crawl_ledger.r2 import Reputation, auditors, final_verdict
from crawl_ledger.record import GENESIS, sha256_hex
from crawl_ledger.ring import Ring
from crawl_ledger.verify import verify_record_r1


def ids(n):
    return sorted(sha256_hex(str(i).encode()) for i in range(n))


def setup(n=6):
    peers = [Peer(f"10.0.0.{i}:7000", rng=random.Random(i)) for i in range(n)]
    return peers, Directory(peers), Ring(peers)


class TestMerkle(unittest.TestCase):
    def test_every_branch_verifies_for_any_size(self):
        for n in range(1, 10):
            leaves = ids(n)
            root = merkle_root(leaves)
            for i, leaf in enumerate(leaves):
                self.assertEqual(root_from_branch(leaf, merkle_branch(leaves, i)), root)

    def test_matches_independent_computation(self):
        a, b, c = ids(3)

        def h(x, y):
            return sha256_hex(bytes.fromhex(x) + bytes.fromhex(y))

        self.assertEqual(merkle_root([a, b, c]), h(h(a, b), h(c, c)))  # odd: last leaf duplicated

    def test_wrong_leaf_fails(self):
        leaves = ids(5)
        self.assertNotEqual(
            root_from_branch(sha256_hex(b"x"), merkle_branch(leaves, 2)), merkle_root(leaves)
        )


class TestBlocks(unittest.TestCase):
    def test_header_chain_and_finality(self):
        peers, d, _ = setup()
        chain = HeaderChain(d, final_depth=2)
        hs = []
        for e in range(4):
            h, _ = build_header(e, ids(3 + e), peers[e], chain.head)
            self.assertTrue(chain.add(h))
            hs.append(h)
        self.assertEqual(chain.head, hs[-1].id)
        self.assertTrue(chain.is_final(hs[1]))
        self.assertFalse(chain.is_final(hs[2]))

    def test_rejects_tampered_header_and_orphan(self):
        peers, d, _ = setup()
        chain = HeaderChain(d)
        h, _ = build_header(0, ids(3), peers[0], GENESIS)
        h.root = sha256_hex(b"other")
        self.assertFalse(chain.add(h))
        orphan, _ = build_header(1, ids(3), peers[0], sha256_hex(b"unknown"))
        self.assertFalse(chain.add(orphan))

    def test_longest_chain_wins(self):
        peers, d, _ = setup()
        chain = HeaderChain(d)
        a, _ = build_header(0, ids(2), peers[0], GENESIS)
        b, _ = build_header(0, ids(3), peers[1], GENESIS)  # fork at the same height
        chain.add(a)
        chain.add(b)
        self.assertEqual(chain.head, a.id)  # tie keeps first seen
        b2, _ = build_header(1, ids(3), peers[1], b.id)
        chain.add(b2)
        self.assertEqual(chain.head, b2.id)
        self.assertFalse(chain.contains(a))

    def test_consumer_verifies_record_without_peer_log(self):
        peers, d, _ = setup()
        recs = [peers[1].make_record(f"p{i}", "pypi", f"u{i}", b"c")[0] for i in range(5)]
        peers[1].log = []  # the owner's log is not needed
        chain = HeaderChain(d)
        rids = [r.id for r in recs]
        hdr, sorted_ids = build_header(0, rids, peers[2], GENESIS)
        chain.add(hdr)
        r = recs[3]
        branch = merkle_branch(sorted_ids, sorted_ids.index(r.id))
        self.assertTrue(verify_record_r1(r, branch, hdr, chain, d))
        r.hash = "0" * 64  # tampered record
        self.assertFalse(verify_record_r1(r, branch, hdr, chain, d))
        outsider = peers[1].make_record("zz", "pypi", "uz", b"c")[0]
        self.assertFalse(verify_record_r1(outsider, branch, hdr, chain, d))  # not in this block


class TestR2(unittest.TestCase):
    def test_auditors_deterministic_and_exclude_owner(self):
        peers, _, ring = setup(10)
        rec = peers[0].make_record("x", "pypi", "u", b"c")[0]
        a1 = auditors(rec, "aa" * 32, ring, 3)
        self.assertEqual([p.id for p in a1], [p.id for p in auditors(rec, "aa" * 32, ring, 3)])
        self.assertEqual(len(a1), 3)
        self.assertNotIn(rec.peer_id, [p.id for p in a1])

    def test_final_verdict(self):
        self.assertEqual(final_verdict([PASS, FAIL, FAIL]), FAIL)
        self.assertEqual(final_verdict([PASS, INCONCLUSIVE, INCONCLUSIVE]), PASS)
        self.assertEqual(final_verdict([INCONCLUSIVE] * 3), INCONCLUSIVE)
        self.assertEqual(final_verdict([PASS, FAIL]), INCONCLUSIVE)

    def test_reputation_rules(self):
        rep = Reputation(["a", "b"], Config(min_audits=2))
        rep.settle("a", [FAIL, FAIL, PASS])
        self.assertEqual(rep.rep["a"], 0.5)
        rep.settle("a", [PASS, PASS, PASS])
        self.assertAlmostEqual(rep.rep["a"], 0.51)
        rep.settle("b", [PASS, PASS, PASS])
        self.assertEqual(rep.rep["b"], 1.0)  # capped
        rep.settle("a", [FAIL, FAIL, FAIL])
        rep.settle("a", [FAIL, FAIL, FAIL])
        self.assertFalse(rep.eligible("a"))

    def test_flagging_by_inconclusive_share(self):
        rep = Reputation(["a", "b", "c"], Config(min_audits=5, inconc_margin=0.2))
        for _ in range(10):
            rep.settle("a", [INCONCLUSIVE] * 3)
            rep.settle("b", [PASS] * 3)
            rep.settle("c", [PASS] * 3)
        self.assertEqual(rep.flagged(), {"a"})


class TestSim(unittest.TestCase):
    ARGS = [
        "--peers",
        "20",
        "--limit",
        "25",
        "--versions",
        "1",
        "--dishonest",
        "0.3",
        "--forge-rate",
        "0.5",
        "--audit-rate",
        "1.0",
        "--change-rate",
        "0",
        "--no-collude",
        "--page-bytes",
        "2000",
    ]

    def test_all_forgeries_caught_without_collusion_or_drift(self):
        for r in sim_m3.main(["--compare"] + self.ARGS):
            self.assertGreater(r["forged_audited"], 0)
            self.assertEqual(r["detection_rate"], 1.0, r["variant"])
            self.assertEqual(r["false_rejection_rate"], 0.0, r["variant"])

    def test_r1_proof_is_smaller_than_refetching_the_page(self):
        r = sim_m3.main(
            ["--variant", "r1"] + self.ARGS + ["--versions", "4", "--page-bytes", "50000"]
        )[0]
        self.assertLess(r["consumer_bytes_ledger_proof"], r["consumer_bytes_refetch_page"])
        self.assertEqual(r["latency_epochs_to_final"], 2)

    def test_signalled_change_is_inconclusive_not_rejection(self):
        args = [x for x in self.ARGS if x != "--no-collude"] + ["--change-rate", "1.0"]
        r = sim_m3.main(["--variant", "baseline"] + args)[0]
        self.assertEqual(r["false_rejection_rate"], 0.0)
        self.assertEqual(r["honest_inconclusive_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
