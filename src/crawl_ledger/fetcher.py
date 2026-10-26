"""Source router, polite downloader and link extractor (the M2 crawler components).

Sources: PyPI JSON API, Read the Docs (discovered from PyPI project URLs),
docs.python.org (standard-library modules), GitHub README via the REST API
(only when GITHUB_TOKEN is set, because unauthenticated access allows 60 requests/hour)."""

import json
import os
import re
import sys
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

USER_AGENT = "crawl-ledger-course-project/0.1 (CS 6675 research prototype)"
STDLIB = set(getattr(sys, "stdlib_module_names", ()))


@dataclass
class FetchResult:
    status: int
    content: bytes
    changed_at: float | None  # the source's own "last changed" signal, if any


def source_urls(pkg: str) -> dict:
    """Source-Type Router: first-hop URLs for a package identifier."""
    if pkg in STDLIB:
        return {"pydocs": f"https://docs.python.org/3/library/{pkg}.html"}
    return {"pypi": f"https://pypi.org/pypi/{pkg}/json"}


def extract_links(source: str, content: bytes) -> dict:
    """URL/Package Extractor: Read the Docs and GitHub links found in PyPI metadata."""
    out = {}
    if source != "pypi":
        return out
    try:
        info = json.loads(content).get("info", {})
    except ValueError:
        return out
    urls = list((info.get("project_urls") or {}).values()) + [info.get("home_page") or ""]
    for u in urls:
        if not u:
            continue
        m = re.match(r"https?://([a-z0-9-]+)\.readthedocs\.io", u)
        if m and "rtd" not in out:
            out["rtd"] = f"https://{m.group(1)}.readthedocs.io/en/latest/"
        m = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", u)
        if m and "github" not in out and os.environ.get("GITHUB_TOKEN"):
            repo = m.group(2).removesuffix(".git")
            out["github"] = f"https://api.github.com/repos/{m.group(1)}/{repo}/readme"
    return out


def _pypi_latest_upload(content: bytes) -> float | None:
    try:
        rel = json.loads(content).get("releases", {})
    except ValueError:
        return None
    times = [
        f["upload_time_iso_8601"]
        for files in rel.values()
        for f in files
        if f.get("upload_time_iso_8601")
    ]
    if not times:
        return None
    return max(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() for t in times)


class Fetcher:
    def __init__(self, delay: float = 1.0, timeout: float = 20.0):
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        token = os.environ.get("GITHUB_TOKEN")
        self._github_headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.requests_made = 0

    def _allowed(self, url: str) -> bool:
        host = urlparse(url).netloc
        if host == "api.github.com":
            return True  # API access governed by GitHub's API terms and rate limits
        if host == "pypi.org" and re.fullmatch(r"/pypi/[^/]+/json", urlparse(url).path):
            # documented JSON API; robots.txt disallows it for generic crawlers, but it is meant
            # for programmatic use. Still fetched politely (delay, User-Agent).
            return True
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.session.get(f"https://{host}/robots.txt", timeout=self.timeout)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
                self._robots[host] = rp
            except requests.RequestException:
                self._robots[host] = None
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    def _polite_wait(self, host: str):
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.delay - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[host] = time.time()

    def fetch(self, url: str) -> FetchResult | None:
        if not self._allowed(url):
            return None
        host = urlparse(url).netloc
        self._polite_wait(host)
        headers = self._github_headers if host == "api.github.com" else {}
        try:
            r = self.session.get(url, timeout=self.timeout, headers=headers)
        except requests.RequestException:
            return None
        self.requests_made += 1
        changed = None
        if "pypi.org/pypi/" in url and r.status_code == 200:
            changed = _pypi_latest_upload(r.content)
        elif r.headers.get("Last-Modified"):
            try:
                changed = parsedate_to_datetime(r.headers["Last-Modified"]).timestamp()
            except (TypeError, ValueError):
                changed = None
        return FetchResult(r.status_code, r.content, changed)


class OfflineFetcher:
    """Deterministic stand-in used by tests: content depends only on the URL."""

    def __init__(self, changed: dict | None = None):
        self.changed = changed or {}
        self.requests_made = 0

    def fetch(self, url: str) -> FetchResult:
        self.requests_made += 1
        bump = self.changed.get(url)
        body = f"content of {url}" + (" v2" if bump else "")
        return FetchResult(200, body.encode(), bump)
