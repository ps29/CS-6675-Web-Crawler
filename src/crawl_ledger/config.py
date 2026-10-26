"""Tunable parameters (M3 Section 4.4).

Audit rate, auditors per record, epoch length and reputation weights."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    epoch_size: int = 50  # records per block
    final_depth: int = 2  # a block is final after this many later blocks
    auditors: int = 3  # s: auditors per record under R2
    pass_gain: float = 0.01  # placeholder, to tune
    fail_factor: float = 0.5  # placeholder, to tune
    eligibility: float = 0.5  # minimum reputation to propose a block
    inconc_margin: float = 0.2  # flag if inconclusive share exceeds the mean by this much
    min_audits: int = 20  # audited records needed before a peer can be flagged

    @classmethod
    def from_json(cls, path: str) -> "Config":
        return cls(**json.loads(Path(path).read_text()))
