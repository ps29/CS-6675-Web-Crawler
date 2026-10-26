"""Load and validate the YAML crawl configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_USER_AGENT = "focused-crawler-course-project/0.1 (CS 6675 research prototype)"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class CrawlSettings:
    max_pages: int = 500
    max_depth: int = 6
    per_host_max: int = 200
    delay_seconds: float = 1.0
    timeout_seconds: float = 15.0
    max_retries: int = 2
    user_agent: str = DEFAULT_USER_AGENT


@dataclass(frozen=True)
class RankingWeights:
    w_tf: float = 1.0
    w_field: float = 3.0
    w_recency: float = 0.5


@dataclass(frozen=True)
class Config:
    domain: str
    seeds: list[str]
    crawl: CrawlSettings = field(default_factory=CrawlSettings)
    db_path: Path = Path("data/crawl.db")
    selectors: dict[str, dict[str, str]] = field(default_factory=dict)
    ranking: RankingWeights = field(default_factory=RankingWeights)


def _build(cls, raw: dict[str, Any] | None, section: str):
    raw = raw or {}
    allowed = cls.__dataclass_fields__.keys()
    unknown = set(raw) - set(allowed)
    if unknown:
        raise ConfigError(f"unknown keys in '{section}': {sorted(unknown)}")
    return cls(**raw)


def parse_config(raw: dict[str, Any]) -> Config:
    domain = (raw.get("domain") or "").strip().lower()
    if not domain:
        raise ConfigError("'domain' is required")
    seeds = raw.get("seeds") or []
    if not seeds:
        raise ConfigError("at least one seed URL is required")

    crawl = _build(CrawlSettings, raw.get("crawl"), "crawl")
    if crawl.delay_seconds < 1.0:
        raise ConfigError("crawl.delay_seconds must be >= 1.0 (politeness)")
    if crawl.max_pages < 1 or crawl.max_depth < 0:
        raise ConfigError("crawl.max_pages must be >= 1 and crawl.max_depth >= 0")

    selectors = {k.upper(): dict(v) for k, v in (raw.get("selectors") or {}).items()}
    db_path = Path((raw.get("storage") or {}).get("db_path", "data/crawl.db"))
    return Config(
        domain=domain,
        seeds=list(seeds),
        crawl=crawl,
        db_path=db_path,
        selectors=selectors,
        ranking=_build(RankingWeights, raw.get("ranking"), "ranking"),
    )


def load_config(path: str | Path) -> Config:
    with open(path, encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f) or {})
