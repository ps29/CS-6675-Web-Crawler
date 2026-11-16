"""Scoring of the duplicate-collapsing experiment."""

import pytest

pytest.importorskip("numpy")

from corpus_jobs.dup_eval import DUP, mirror_url, score  # noqa: E402


def test_mirror_url_is_on_the_same_host():
    assert mirror_url("https://a.org/x/y.html#f") == "https://a.org/mirror/x/y.html"
    assert mirror_url("https://a.org") == "https://a.org/mirror/"


def test_group_aware_score_gives_nothing_for_a_repeat():
    rel = {"o1": 2, "o2": 1}
    canon = {"m1": "o1"}
    s = score(["o1", "m1", "o2"], rel, canon)
    assert s["redundant@10"] == 1
    assert s["plain_p@5"] == 3 / 5  # the mirror counts as relevant in the plain view
    assert s["group_p@5"] == 2 / 5  # and as nothing in the group-aware view
    collapsed = score(["o1", "o2"], rel, canon)
    assert collapsed["group_ndcg@10"] > s["group_ndcg@10"]
    assert DUP not in rel
