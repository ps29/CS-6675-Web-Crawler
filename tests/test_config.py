from pathlib import Path

import pytest

from focused_crawler.config import ConfigError, load_config, parse_config

ROOT = Path(__file__).resolve().parents[1]


def test_default_config_loads():
    cfg = load_config(ROOT / "config" / "default.yaml")
    assert cfg.domain == "gatech.edu"
    assert cfg.seeds
    assert cfg.crawl.delay_seconds >= 1.0
    assert "FACULTY" in cfg.selectors


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
