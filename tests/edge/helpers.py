from crawl_ledger.blocks import HeaderChain
from edge_serving.ledger_view import Ledger


class Clock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def consumer_chain(ledger: Ledger) -> HeaderChain:
    """A consumer's own copy of the header chain, built from gossiped headers."""
    chain = HeaderChain(ledger.directory, ledger.chain.final_depth)
    for hdr in ledger.headers:
        assert chain.add(hdr)
    return chain
