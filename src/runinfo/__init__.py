"""Provenance for results files: which code, configuration and interpreter produced them.

Every experiment that writes a results JSON adds `"provenance": provenance(vars(args))`, so a
number in a report can be traced to the exact commit and options that produced it. `git_dirty`
is true when the working tree had uncommitted changes, in which case the commit alone does not
identify the code."""

from __future__ import annotations

import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def provenance(config: dict | None = None) -> dict:
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain") if commit else None
    return {
        "git_commit": commit,
        "git_dirty": bool(status) if status is not None else None,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "argv": sys.argv[1:],
        "config": {
            k: v if isinstance(v, int | float | str | bool | list | None) else str(v)
            for k, v in (config or {}).items()
        },  # fmt: skip
    }
