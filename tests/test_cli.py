import json

from focused_crawler.classifier import COURSE
from focused_crawler.cli import main
from focused_crawler.storage import Storage


def seed_db(path):
    with Storage(path) as s:
        pid = s.upsert_page(
            "https://a.gatech.edu/courses/cs6675", "a.gatech.edu", 0, 200, "text/html",
            title="CS 6675", label=COURSE, fields={"course_code": "CS 6675"},
        )  # fmt: skip
        s.replace_postings(pid, {"cs6675": [0], "cs": [1], "6675": [2]})


def test_search_json(tmp_path, capsys):
    db = tmp_path / "t.db"
    seed_db(db)
    assert main(["--db", str(db), "search", "CS 6675", "--json"]) == 0
    results = json.loads(capsys.readouterr().out)
    assert results[0]["fields"]["course_code"] == "CS 6675"


def test_stats_and_export_csv(tmp_path, capsys):
    db = tmp_path / "t.db"
    seed_db(db)
    assert main(["--db", str(db), "stats"]) == 0
    assert json.loads(capsys.readouterr().out)["labels"] == {COURSE: 1}
    out = tmp_path / "pages.csv"
    assert main(["--db", str(db), "export", "--format", "csv", "--out", str(out)]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("id,url,label") and "cs6675" in lines[1]


def test_bad_config_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("seeds: []\n", encoding="utf-8")
    assert main(["--config", str(bad), "stats"]) == 2
