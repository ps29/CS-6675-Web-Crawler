from focused_crawler.frontier import Frontier


def test_pops_highest_priority_then_fifo():
    f = Frontier(max_depth=5, per_host_max=100)
    f.push("https://a.edu/low", 0.1, 1)
    f.push("https://a.edu/high", 0.9, 1)
    f.push("https://a.edu/mid1", 0.5, 1)
    f.push("https://a.edu/mid2", 0.5, 1)
    order = [f.pop().url for _ in range(4)]
    assert order == [
        "https://a.edu/high",
        "https://a.edu/mid1",
        "https://a.edu/mid2",
        "https://a.edu/low",
    ]
    assert f.pop() is None


def test_rejects_duplicates_and_deep_urls():
    f = Frontier(max_depth=2, per_host_max=100)
    assert f.push("https://a.edu/x", 1.0, 0)
    assert not f.push("https://a.edu/x", 1.0, 1)
    assert not f.push("https://a.edu/deep", 1.0, 3)
    assert len(f) == 1


def test_per_host_cap():
    f = Frontier(max_depth=5, per_host_max=2)
    for i in range(3):
        f.push(f"https://a.edu/{i}", 1.0, 0)
    f.push("https://b.edu/", 0.5, 0)
    popped = []
    while (item := f.pop()) is not None:
        popped.append(item.url)
    assert popped == ["https://a.edu/0", "https://a.edu/1", "https://b.edu/"]
