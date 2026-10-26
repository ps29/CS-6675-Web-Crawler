"""URL normalization, scope checks and hashing (the URL Resolver of the M1 design)."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "sessionid",
    "session_id",
    "sid",
    "jsessionid",
    "phpsessid",
    "aspsessionid",
    "_ga",
    "ref",
}
SKIP_EXTENSIONS = re.compile(
    r"\.(?:jpe?g|png|gif|svg|webp|ico|bmp|tiff?|mp[34]|m4a|wav|avi|mov|wmv|webm|zip|gz|tar|rar|7z"
    r"|exe|msi|dmg|iso|css|js|json|xml|rss|woff2?|ttf|eot|docx?|xlsx?|pptx?|pdf|ics)$",
    re.IGNORECASE,
)
_PATH_SESSION = re.compile(r";(?:jsessionid|phpsessid|sid)=[^/?#]*", re.IGNORECASE)


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Return a canonical http(s) URL, or None if the URL cannot be crawled."""
    url = url.strip()
    if not url or url.startswith(("mailto:", "tel:", "javascript:", "data:")):
        return None
    if base:
        url = urljoin(base, url)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None

    host = parts.hostname.lower().rstrip(".")
    if port and not (scheme == "http" and port == 80 or scheme == "https" and port == 443):
        host = f"{host}:{port}"

    path = _PATH_SESSION.sub("", parts.path) or "/"
    path = re.sub(r"/{2,}", "/", path)
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, host, path, query, ""))


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def in_scope(url: str, domain: str) -> bool:
    """True if the URL's host is the domain itself or one of its subdomains."""
    host = (urlsplit(url).hostname or "").lower()
    domain = domain.lower()
    return host == domain or host.endswith("." + domain)


def is_crawlable_resource(url: str) -> bool:
    return not SKIP_EXTENSIONS.search(urlsplit(url).path)


def url_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()
