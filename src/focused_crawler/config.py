"""Load and validate the YAML crawl configuration."""

from __future__ import annotations

import re
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
    host_penalty: float = 0.0  # priority lost per page already crawled on a host
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
    domains: tuple[str, ...]
    seeds: list[str]
    profile: str = "academic"
    exclude: tuple[str, ...] = ()
    crawl: CrawlSettings = field(default_factory=CrawlSettings)
    db_path: Path = Path("data/crawl.db")
    selectors: dict[str, dict[str, str]] = field(default_factory=dict)
    ranking: RankingWeights = field(default_factory=RankingWeights)

    @property
    def domain(self) -> str:
        return self.domains[0]


def _build(cls, raw: dict[str, Any] | None, section: str):
    raw = raw or {}
    allowed = cls.__dataclass_fields__.keys()
    unknown = set(raw) - set(allowed)
    if unknown:
        raise ConfigError(f"unknown keys in '{section}': {sorted(unknown)}")
    return cls(**raw)


def _domains(raw: dict[str, Any]) -> tuple[str, ...]:
    """'domain' (one scope) or 'domains' (a list). A scope is a host, optionally with a path."""
    listed = raw.get("domains") or []
    if isinstance(listed, str):
        listed = [listed]
    if raw.get("domain"):
        listed = [raw["domain"], *listed]
    domains = tuple(
        d.strip().lower().removeprefix("https://").removeprefix("http://") for d in listed
    )
    domains = tuple(d for d in domains if d)
    if not domains:
        raise ConfigError("'domain' or 'domains' is required")
    return domains


def parse_config(raw: dict[str, Any]) -> Config:
    from .profiles import PROFILES

    domains = _domains(raw)
    seeds = raw.get("seeds") or []
    if not seeds:
        raise ConfigError("at least one seed URL is required")
    profile = raw.get("profile", "academic")
    if profile not in PROFILES:
        raise ConfigError(f"unknown profile {profile!r}; choose from {sorted(PROFILES)}")
    exclude = tuple(raw.get("exclude") or ())
    for pattern in exclude:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ConfigError(f"bad exclude pattern {pattern!r}: {e}") from e

    crawl = _build(CrawlSettings, raw.get("crawl"), "crawl")
    if crawl.delay_seconds < 1.0:
        raise ConfigError("crawl.delay_seconds must be >= 1.0 (politeness)")
    if crawl.max_pages < 1 or crawl.max_depth < 0:
        raise ConfigError("crawl.max_pages must be >= 1 and crawl.max_depth >= 0")

    selectors = {k.upper(): dict(v) for k, v in (raw.get("selectors") or {}).items()}
    db_path = Path((raw.get("storage") or {}).get("db_path", "data/crawl.db"))
    return Config(
        domains=domains,
        seeds=list(seeds),
        profile=profile,
        exclude=exclude,
        crawl=crawl,
        db_path=db_path,
        selectors=selectors,
        ranking=_build(RankingWeights, raw.get("ranking"), "ranking"),
    )


def load_config(path: str | Path) -> Config:
    with open(path, encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f) or {})
