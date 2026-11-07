from pathlib import Path

import pytest

from focused_crawler.config import ConfigError, load_config, parse_config

ROOT = Path(__file__).resolve().parents[1]


def test_default_config_targets_python_docs():
    cfg = load_config(ROOT / "config" / "default.yaml")
    assert cfg.profile == "python_docs"
    assert cfg.domain == "docs.python.org/3/"
    assert len(cfg.domains) >= 10
    assert cfg.crawl.delay_seconds >= 1.0
    from focused_crawler.url_utils import in_any_scope

    assert all(in_any_scope(seed, cfg.domains) for seed in cfg.seeds)


def test_academic_config_loads():
    cfg = load_config(ROOT / "config" / "academic.yaml")
    assert cfg.profile == "academic"
    assert cfg.domains == ("gatech.edu",)
    assert "FACULTY" in cfg.selectors
    assert cfg.exclude


def test_domains_list_and_scheme_stripped():
    cfg = parse_config({"domains": ["https://a.org/doc/", "b.io"], "seeds": ["https://a.org/doc/"]})
    assert cfg.domains == ("a.org/doc/", "b.io")
    assert cfg.profile == "academic"


def test_rejects_unknown_profile_and_bad_exclude():
    base = {"domain": "x.edu", "seeds": ["https://x.edu/"]}
    with pytest.raises(ConfigError):
        parse_config({**base, "profile": "nope"})
    with pytest.raises(ConfigError):
        parse_config({**base, "exclude": ["("]})


def test_rejects_missing_domain():
    with pytest.raises(ConfigError):
        parse_config({"seeds": ["https://x.edu/"]})


def test_rejects_impolite_delay():
    with pytest.raises(ConfigError):
        parse_config(
            {"domain": "x.edu", "seeds": ["https://x.edu/"], "crawl": {"delay_seconds": 0}}
        )


def test_rejects_unknown_keys():
    with pytest.raises(ConfigError):
        parse_config({"domain": "x.edu", "seeds": ["https://x.edu/"], "crawl": {"speed": 9}})
