"""Results files carry provenance."""

import json
import re

from focused_crawler import evaluate
from runinfo import provenance


def test_provenance_fields():
    p = provenance({"seed": 1, "path": object()})
    assert set(p) == {"git_commit", "git_dirty", "python", "platform", "created_utc", "argv",
                      "config"}  # fmt: skip
    assert p["git_commit"] is None or re.fullmatch(r"[0-9a-f]{40}", p["git_commit"])
    assert p["config"]["seed"] == 1 and isinstance(p["config"]["path"], str)
    json.dumps(p)


def test_evaluate_writes_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "evaluate", lambda storage, queries: {"queries": 0})
    monkeypatch.setattr(evaluate, "print_report", lambda result, status: None)
    queries = tmp_path / "q.yaml"
    queries.write_text("status: draft\nqueries: []\n")
    out = tmp_path / "out.json"
    evaluate.main(["--db", str(tmp_path / "x.db"), "--queries", str(queries), "--out", str(out)])
    data = json.loads(out.read_text())
    assert data["provenance"]["config"]["queries"] == str(queries)
