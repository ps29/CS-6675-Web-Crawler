import random
import unittest

from crawl_ledger.audit import FAIL, INCONCLUSIVE, PASS, audit
from crawl_ledger.fetcher import OfflineFetcher, extract_links
from crawl_ledger.peer import Directory, Peer
from crawl_ledger.ring import Ring
from crawl_ledger.verify import verify


def setup(n=5, forge_rate=0.0):
    peers = [Peer(f"10.0.0.{i}:7000", rng=random.Random(i)) for i in range(n)]
    peers[0].forge_rate = forge_rate
    return peers, Directory(peers)


class TestBaseline(unittest.TestCase):
    def test_honest_record_verifies_and_passes_audit(self):
        peers, d = setup()
        f = OfflineFetcher()
        url = "https://pypi.org/pypi/requests/json"
        rec, forged = peers[1].make_record("requests", "pypi", url, f.fetch(url).content)
        self.assertFalse(forged)
        self.assertTrue(verify(rec, d))
        self.assertEqual(audit(rec, d, f), PASS)

    def test_tampered_record_fails_verification(self):
        peers, d = setup()
        rec, _ = peers[1].make_record("x", "pypi", "u", b"abc")
        rec.hash = "0" * 64  # edit after signing
        self.assertFalse(verify(rec, d))

    def test_rewriting_old_record_breaks_chain(self):
        peers, d = setup()
        p = peers[1]
        r1, _ = p.make_record("a", "pypi", "u1", b"1")
        r2, _ = p.make_record("b", "pypi", "u2", b"2")
        r1.url = "evil"
        r1.id = r1.compute_id()  # attacker recomputes the id...
        self.assertFalse(verify(r2, d))  # ...but r2.prev no longer matches

    def test_forged_record_is_caught(self):
        peers, d = setup(forge_rate=1.0)
        f = OfflineFetcher()
        rec, forged = peers[0].make_record("x", "pypi", "u", f.fetch("u").content)
        self.assertTrue(forged)
        self.assertTrue(verify(rec, d))  # validly signed, but content is false
        self.assertEqual(audit(rec, d, f), FAIL)

    def test_source_change_is_inconclusive_not_fail(self):
        peers, d = setup()
        url = "u"
        rec, _ = peers[1].make_record("x", "pypi", url, OfflineFetcher().fetch(url).content)
        changed = OfflineFetcher(changed={url: rec.time + 60})
        self.assertEqual(audit(rec, d, changed), INCONCLUSIVE)

    def test_ring_ownership_and_successors(self):
        peers, _ = setup(n=8)
        ring = Ring(peers)
        owner = ring.owner("requests")
        self.assertIn(owner, peers)
        succ = ring.successors(owner.id, 3, exclude=owner.id)
        self.assertEqual(len(succ), 3)
        self.assertNotIn(owner, succ)

    def test_extract_links_from_pypi_metadata(self):
        body = b'{"info": {"project_urls": {"Docs": "https://requests.readthedocs.io"}}}'
        self.assertEqual(
            extract_links("pypi", body)["rtd"], "https://requests.readthedocs.io/en/latest/"
        )


if __name__ == "__main__":
    unittest.main()
