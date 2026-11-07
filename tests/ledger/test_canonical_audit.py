"""Canonical, block-level audits: build noise passes, forged edits to sensitive content fail."""

import json
import random

import pytest

from crawl_ledger.audit import FAIL, INCONCLUSIVE, PASS
from crawl_ledger.canonical import CODE, LINK, digest, hamming, simhash64
from crawl_ledger.canonical_audit import canonical_audit, claim_root, decide
from crawl_ledger.fetcher import FetchResult
from crawl_ledger.forgery import FORGERIES, forge, forge_content, rebuild_noise
from crawl_ledger.peer import Directory, Peer, make_canonical_record
from crawl_ledger.record import Record, sha256_hex

PAGE = b"""<html><head><title>requests</title>
<script src="_static/doctools.js?v=1a2b3c4d"></script></head>
<body><nav><a href="/">Home</a></nav>
<div role="main">
<h1>Installation</h1>
<p>To install Requests, simply run this command in your terminal of choice:</p>
<pre><span>$ </span>pip install requests</pre>
<p>The library is available on <a href="https://github.com/psf/requests">GitHub</a>.</p>
<p>Requests is an elegant and simple HTTP library for Python, built for human beings.</p>
<p>Version 2.32.3 is the latest release.</p>
</div>
<footer><p>&copy; Copyright 2026. Last updated on Nov 7, 2026.</p></footer>
</body></html>"""

PYPI = json.dumps(
    {
        "info": {
            "name": "requests",
            "version": "2.32.3",
            "summary": "Python HTTP for Humans.",
            "project_urls": {"Documentation": "https://requests.readthedocs.io"},
            "requires_dist": ["charset_normalizer<4,>=2"],
        },
        "last_serial": 123456,
        "urls": [],
    }
).encode()


class Stub:
    def __init__(self, *bodies, changed_at=None):
        self.bodies = list(bodies)
        self.changed_at = changed_at
        self.calls = 0

    def fetch(self, url):
        body = self.bodies[min(self.calls, len(self.bodies) - 1)]
        self.calls += 1
        return FetchResult(200, body, self.changed_at)


def peers():
    ps = [Peer(f"10.0.0.{i}:7000", rng=random.Random(i)) for i in range(2)]
    return ps, Directory(ps)


def test_digest_keeps_content_and_drops_chrome():
    d = digest(PAGE)
    kinds = {(b.kind, b.text) for b in d.blocks}
    assert (CODE, "$ pip install requests") in kinds
    assert (LINK, "https://github.com/psf/requests") in kinds
    assert (LINK, "script:_static/doctools.js") in kinds  # cache-buster query stripped
    assert not any(b.text == "Home" for b in d.blocks)  # nav dropped
    assert not any("Copyright" in b.text for b in d.blocks)  # footer dropped


def test_rebuild_noise_does_not_change_the_digest():
    assert digest(rebuild_noise(PAGE, 1)).root == digest(rebuild_noise(PAGE, 2)).root
    assert digest(rebuild_noise(PAGE, 1)).root == digest(PAGE).root
    assert digest(rebuild_noise(PYPI, 1)).root == digest(PYPI).root


def test_record_ids_unchanged_without_canon():
    rec = Record("p", "requests", "pypi", "u", "h", 1.0, "0" * 64)
    blob = json.dumps(rec.signed_fields(), sort_keys=True, separators=(",", ":"))
    assert "canon" not in blob and rec.compute_id() == sha256_hex(blob.encode())


def test_claim_root_matches_digest_root():
    d = digest(PAGE)
    assert claim_root(d.version, d.hashes) == d.root


def test_honest_record_passes_despite_rebuild_noise():
    (owner, _), directory = peers()
    rec, hashes, version = make_canonical_record(owner, "requests", "docs", "u", PAGE, now=1.0)
    fetcher = Stub(rebuild_noise(PAGE, 7))
    assert canonical_audit(rec, hashes, version, directory, fetcher).verdict == PASS
    assert fetcher.calls == 1  # a match needs no second fetch


@pytest.mark.parametrize("kind", ["install", "href", "code", "version", "script"])
def test_forged_sensitive_edit_fails(kind):
    (owner, _), directory = peers()
    forged = forge(PAGE, kind)
    rec, hashes, version = make_canonical_record(
        owner, "requests", "docs", "u", PAGE, now=1.0, forged_content=forged
    )
    detail = canonical_audit(rec, hashes, version, directory, Stub(PAGE, PAGE))
    assert detail.verdict == FAIL, detail.reason


@pytest.mark.parametrize("kind", ["json_url", "json_requires", "version"])
def test_forged_pypi_metadata_fails(kind):
    (owner, _), directory = peers()
    rec, hashes, version = make_canonical_record(
        owner, "requests", "pypi", "u", PYPI, now=1.0, forged_content=forge(PYPI, kind)
    )
    assert canonical_audit(rec, hashes, version, directory, Stub(PYPI, PYPI)).verdict == FAIL


def test_prose_edit_is_flagged_not_failed():
    """A negated sentence is not provably malicious: it goes to re-audit as INCONCLUSIVE."""
    (owner, _), directory = peers()
    rec, hashes, version = make_canonical_record(
        owner, "requests", "docs", "u", PAGE, now=1.0, forged_content=forge(PAGE, "prose")
    )
    assert canonical_audit(rec, hashes, version, directory, Stub(PAGE, PAGE)).verdict == (
        INCONCLUSIVE
    )


def test_unstable_page_is_inconclusive():
    d = digest(PAGE)
    other = PAGE.replace(b"simply run", b"run")
    assert decide(d.hashes, d.version, other, PAGE, changed=False).verdict == INCONCLUSIVE


def test_source_change_signal_is_inconclusive():
    d = digest(forge(PAGE, "install"))
    assert decide(d.hashes, d.version, PAGE, PAGE, changed=True).verdict == INCONCLUSIVE


def test_tampered_claim_list_fails():
    (owner, _), directory = peers()
    rec, hashes, version = make_canonical_record(owner, "requests", "docs", "u", PAGE, now=1.0)
    assert canonical_audit(rec, hashes[:-1], version, directory, Stub(PAGE)).verdict == FAIL


def test_simhash_accepts_a_typosquat():
    """Why SimHash is only a triage signal: a one-token forgery sits within 3 bits."""
    a = digest(PAGE).text()
    b = digest(forge(PAGE, "install")).text()
    assert a != b and hamming(simhash64(a), simhash64(b)) <= 6


def test_every_forgery_kind_applies_to_some_fixture():
    assert all(forge(PAGE, k) is not None or forge(PYPI, k) is not None for k in FORGERIES)


@pytest.mark.parametrize("kind", ["install", "href", "code", "version", "prose"])
def test_content_forgery_edits_kept_content(kind):
    page = PAGE.replace(b"<title>requests</title>", b'<link href="https://cdn.example/a.css">')
    forged = forge_content(page, kind)
    assert forged is not None and b"cdn.example/a.css" in forged  # head left alone
    assert digest(forged).root != digest(page).root
    assert forge_content(PYPI, kind) is None


def test_content_forgery_skips_chrome_links():
    page = PAGE.replace(b'<a href="/">Home</a>', b'<a href="https://x.example/">Home</a>')
    assert b"https://x.example/" in forge_content(page, "href")  # nav link untouched


def test_text_outside_block_tags_is_kept():
    page = PAGE.replace(
        b"<h1>Installation</h1>",
        b'<h1>Installation</h1><a href="/r">Release 3.11.0</a>'
        b"<ul><li>Notes 1.2.3<ul><li>inner</li></ul></li></ul>",
    )
    texts = {b.text for b in digest(page).blocks}
    assert {"Release 3.11.0", "Notes 1.2.3", "inner"} <= texts
