import pytest

from edge_serving.objects import (
    IMMUTABLE,
    CacheEntry,
    Response,
    parse_cache_control,
    shared_cache_lifetime,
)


def test_parse_cache_control():
    assert parse_cache_control("max-age=31536000, immutable") == {
        "max-age": 31536000,
        "immutable": True,
    }


@pytest.mark.parametrize(
    ("header", "lifetime"),
    [
        (IMMUTABLE, 31536000.0),
        ("s-maxage=60, max-age=10", 60.0),
        ("max-age=5", 5.0),
        ("no-store", None),
        ("private, max-age=60", None),
        ("", None),
    ],
)
def test_shared_cache_lifetime(header, lifetime):
    assert shared_cache_lifetime(header) == lifetime


def test_cache_entry_freshness():
    entry = CacheEntry(Response(200, {}), stored_at=100.0, lifetime=10.0)
    assert entry.fresh(109.9)
    assert not entry.fresh(110.0)
