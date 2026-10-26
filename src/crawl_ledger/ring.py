"""Minimal Chord-style ring: peers ordered by ID; a key is owned by its successor.
Lookups are computed directly (no finger tables) because all peers run in one process."""

from .record import sha256_hex


class Ring:
    def __init__(self, peers):
        self.peers = sorted(peers, key=lambda p: int(p.id, 16))

    def successor(self, key_hex: str):
        k = int(key_hex, 16)
        for p in self.peers:
            if int(p.id, 16) >= k:
                return p
        return self.peers[0]

    def owner(self, pkg: str):
        return self.successor(sha256_hex(pkg.encode()))

    def successors(self, key_hex: str, s: int, exclude: str | None = None):
        start = self.peers.index(self.successor(key_hex))
        out = []
        for i in range(len(self.peers)):
            p = self.peers[(start + i) % len(self.peers)]
            if p.id != exclude:
                out.append(p)
            if len(out) == s:
                break
        return out
