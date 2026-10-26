"""Focused crawl loop (M1 Section 3.4): pop -> fetch -> classify -> extract -> index -> enqueue."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from .classifier import OTHER, RuleClassifier, score_link
from .config import Config
from .extractor import extract_fields
from .fetcher import Fetcher
from .frontier import Frontier, FrontierItem
from .indexer import content_hash, term_positions
from .parser import parse_html
from .storage import Storage
from .url_utils import host_of, in_scope, is_crawlable_resource, normalize_url

log = logging.getLogger(__name__)
COMMIT_EVERY = 20
SEED_PRIORITY = 1.0


@dataclass
class CrawlStats:
    fetched: int = 0
    html_pages: int = 0
    blocked_or_failed: int = 0
    non_html: int = 0
    redirected_away: int = 0
    errors: int = 0
    labels: Counter = field(default_factory=Counter)

    @property
    def useful_yield(self) -> float:
        """Share of HTML pages labeled COURSE/FACULTY/LAB (M1 Section 5.1)."""
        if not self.html_pages:
            return 0.0
        return 1.0 - self.labels[OTHER] / self.html_pages


class Crawler:
    def __init__(
        self,
        config: Config,
        storage: Storage,
        fetcher: Fetcher | None = None,
        classifier: RuleClassifier | None = None,
    ):
        self.config = config
        self.storage = storage
        settings = config.crawl
        self.fetcher = fetcher or Fetcher(
            user_agent=settings.user_agent,
            delay=settings.delay_seconds,
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )
        self.classifier = classifier or RuleClassifier()
        self.frontier = Frontier(settings.max_depth, settings.per_host_max)
        self.stats = CrawlStats()

    def _enqueue(self, url: str, priority: float, depth: int, parent: str | None = None) -> None:
        if in_scope(url, self.config.domain) and is_crawlable_resource(url):
            self.frontier.push(url, priority, depth, parent)

    def run(self, max_pages: int | None = None) -> CrawlStats:
        budget = max_pages or self.config.crawl.max_pages
        for seed in self.config.seeds:
            if norm := normalize_url(seed):
                self._enqueue(norm, SEED_PRIORITY, 0)

        try:
            while self.stats.fetched < budget and (item := self.frontier.pop()) is not None:
                try:
                    self._process(item)
                except Exception:  # one bad page must not stop the crawl
                    self.stats.errors += 1
                    log.exception("failed to process %s", item.url)
                if self.stats.fetched and self.stats.fetched % COMMIT_EVERY == 0:
                    self.storage.commit()
                    log.info(
                        "fetched=%d queued=%d labels=%s",
                        self.stats.fetched,
                        len(self.frontier),
                        dict(self.stats.labels),
                    )
        finally:
            self.storage.commit()
        return self.stats

    def _process(self, item: FrontierItem) -> None:
        result = self.fetcher.fetch(item.url)
        if result is None:
            self.stats.blocked_or_failed += 1
            log.debug("skipped (robots or network): %s", item.url)
            return
        self.stats.fetched += 1

        url = normalize_url(result.final_url) or item.url
        if url != item.url:
            if not in_scope(url, self.config.domain):
                self.stats.redirected_away += 1
                return
            if not self.frontier.mark_seen(url):
                return  # redirect target already crawled or queued

        if not result.ok_html:
            self.stats.non_html += 1
            self.storage.upsert_page(
                url, host_of(url), item.depth, result.status, result.content_type
            )
            return

        page = parse_html(result.text, url)
        verdict = self.classifier.classify(page)
        fields = extract_fields(page, verdict.label, self.config.selectors)
        self.stats.html_pages += 1
        self.stats.labels[verdict.label] += 1

        page_id = self.storage.upsert_page(
            url,
            host_of(url),
            item.depth,
            result.status,
            result.content_type,
            title=page.title,
            description=page.meta.get("description"),
            label=verdict.label,
            label_score=round(verdict.score, 4),
            content_hash=content_hash(page) if page.text else None,
            last_modified=result.last_modified,
            fields=fields,
        )
        self.storage.replace_structured(page_id, verdict.label, fields)
        self.storage.replace_postings(page_id, {} if page.noindex else term_positions(page))
        self.storage.replace_links(page_id, ((link.url, link.anchor) for link in page.links))

        for link in page.links:
            priority = score_link(link.url, link.anchor, link.context, verdict.label)
            self._enqueue(link.url, priority, item.depth + 1, parent=url)
        log.debug("%s %.2f %s", verdict.label, verdict.score, url)
