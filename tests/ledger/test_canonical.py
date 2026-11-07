import random
import unittest

from crawl_ledger.audit import FAIL, INCONCLUSIVE, PASS, audit
from crawl_ledger.fetcher import FetchResult
from crawl_ledger.peer import Directory, Peer
from crawl_ledger.record import canonical_content, content_hash, sha256_hex


def rocket_loader_page(nonce: str, body: str = "<p>docs</p>") -> bytes:
    """Shape of matplotlib.org: Rocket Loader rewrites script types with a per-response nonce."""
    return (
        f'<script src="_static/doctools.js" type="{nonce}-text/javascript"></script>{body}'
        '<script src="/cdn-cgi/scripts/7d0fa10a/cloudflare-static/rocket-loader.min.js" '
        f'data-cf-settings="{nonce}-|49" defer></script>'
    ).encode()


def challenge_page(ray: str, t: str, body: str = "<p>docs</p>") -> bytes:
    """Shape of docs.sqlalchemy.org: the challenge script embeds a ray ID and a timestamp."""
    return (
        f"{body}<script>d.innerHTML=\"window.__CF$cv$params={{r:'{ray}',t:'{t}'}};"
        f"var u='/cdn-cgi/challenge-platform/scripts/jsd/main.js?ray={ray}';\"</script>"
    ).encode()


NONCE_A, NONCE_B = "fa53ebdf02454bcd19a1502c", "16b6ee079bb5b03c9808d506"
RAY_A, RAY_B = "a56c118d9a648e51", "a56c11cc4bca8e51"
T_A, T_B = "MTc5NDA1MTY4Mw==", "MTc5NDA1MTY5Mw=="


class StubFetcher:
    def __init__(self, content: bytes, changed_at: float | None = None):
        self.content = content
        self.changed_at = changed_at

    def fetch(self, url):
        return FetchResult(200, self.content, self.changed_at)


def setup(forge_rate=0.0):
    peers = [Peer(f"10.0.0.{i}:7000", rng=random.Random(i)) for i in range(3)]
    peers[0].forge_rate = forge_rate
    return peers, Directory(peers)


class TestCanonicalContent(unittest.TestCase):
    def test_rocket_loader_nonce_is_ignored(self):
        self.assertEqual(
            content_hash(rocket_loader_page(NONCE_A)), content_hash(rocket_loader_page(NONCE_B))
        )

    def test_challenge_ray_and_time_are_ignored(self):
        self.assertEqual(
            content_hash(challenge_page(RAY_A, T_A)), content_hash(challenge_page(RAY_B, T_B))
        )

    def test_content_without_nonces_is_unchanged(self):
        body = b'{"info": {"name": "requests"}}\n<!-- a comment --><script type="module"></script>'
        self.assertEqual(canonical_content(body), body)
        self.assertEqual(content_hash(body), sha256_hex(body))

    def test_real_content_change_still_differs(self):
        old = rocket_loader_page(NONCE_A, "<p>pip install requests</p>")
        new = rocket_loader_page(NONCE_A, "<p>pip install reqeusts</p>")
        self.assertNotEqual(content_hash(old), content_hash(new))

    def test_script_path_change_still_differs(self):
        # Only the nonce is dropped, so swapping the script behind it is still visible.
        old = rocket_loader_page(NONCE_A)
        new = old.replace(b"_static/doctools.js", b"evil.js")
        self.assertNotEqual(content_hash(old), content_hash(new))

    def test_comment_is_kept(self):
        # A forged record appends a comment, so comments must survive canonicalization.
        page = rocket_loader_page(NONCE_A)
        self.assertNotEqual(content_hash(page), content_hash(page + b"\n<!-- altered -->"))


class TestAuditOnCdnPages(unittest.TestCase):
    def test_honest_record_passes_when_rocket_loader_nonce_rotates(self):
        peers, d = setup()
        rec, _ = peers[1].make_record("matplotlib", "docs", "u", rocket_loader_page(NONCE_A))
        self.assertEqual(audit(rec, d, StubFetcher(rocket_loader_page(NONCE_B))), PASS)

    def test_honest_record_passes_when_challenge_ray_rotates(self):
        peers, d = setup()
        rec, _ = peers[1].make_record("sqlalchemy", "docs", "u", challenge_page(RAY_A, T_A))
        self.assertEqual(audit(rec, d, StubFetcher(challenge_page(RAY_B, T_B))), PASS)

    def test_forged_record_on_nonce_page_still_fails(self):
        peers, d = setup(forge_rate=1.0)
        rec, forged = peers[0].make_record("x", "docs", "u", rocket_loader_page(NONCE_A))
        self.assertTrue(forged)
        self.assertEqual(audit(rec, d, StubFetcher(rocket_loader_page(NONCE_B))), FAIL)

    def test_changed_page_with_source_signal_is_inconclusive(self):
        peers, d = setup()
        rec, _ = peers[1].make_record("x", "docs", "u", rocket_loader_page(NONCE_A))
        changed = StubFetcher(rocket_loader_page(NONCE_B, "<p>new</p>"), rec.time + 60)
        self.assertEqual(audit(rec, d, changed), INCONCLUSIVE)
