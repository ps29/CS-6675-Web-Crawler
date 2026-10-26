"""SQLite storage: page table, link graph, per-type metadata tables and posting lists."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

from .classifier import COURSE, FACULTY, LAB
from .python_docs import STRUCTURED_TABLES as DOC_TABLES

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    id            INTEGER PRIMARY KEY,
    url           TEXT NOT NULL UNIQUE,
    host          TEXT NOT NULL,
    depth         INTEGER NOT NULL,
    status        INTEGER,
    content_type  TEXT,
    title         TEXT,
    description   TEXT,
    label         TEXT,
    label_score   REAL,
    doc_length    INTEGER DEFAULT 0,
    content_hash  TEXT,
    duplicate_of  INTEGER REFERENCES pages(id),
    last_modified REAL,
    fetched_at    REAL NOT NULL,
    fields_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_pages_label ON pages(label);
CREATE INDEX IF NOT EXISTS idx_pages_hash ON pages(content_hash);

CREATE TABLE IF NOT EXISTS links (
    src_id  INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    dst_url TEXT NOT NULL,
    anchor  TEXT,
    PRIMARY KEY (src_id, dst_url)
);
CREATE INDEX IF NOT EXISTS idx_links_dst ON links(dst_url);

CREATE TABLE IF NOT EXISTS courses (
    page_id      INTEGER PRIMARY KEY REFERENCES pages(id) ON DELETE CASCADE,
    course_code  TEXT,
    title        TEXT,
    instructor   TEXT,
    credit_hours TEXT
);
CREATE INDEX IF NOT EXISTS idx_courses_code ON courses(course_code);

CREATE TABLE IF NOT EXISTS faculty (
    page_id           INTEGER PRIMARY KEY REFERENCES pages(id) ON DELETE CASCADE,
    name              TEXT,
    position          TEXT,
    department        TEXT,
    email             TEXT,
    research_keywords TEXT
);

CREATE TABLE IF NOT EXISTS labs (
    page_id    INTEGER PRIMARY KEY REFERENCES pages(id) ON DELETE CASCADE,
    name       TEXT,
    pi         TEXT,
    department TEXT
);

CREATE TABLE IF NOT EXISTS doc_pages (
    page_id       INTEGER PRIMARY KEY REFERENCES pages(id) ON DELETE CASCADE,
    package       TEXT,
    kind          TEXT,
    title         TEXT,
    version       TEXT,
    module        TEXT,
    n_symbols     INTEGER,
    n_code_blocks INTEGER,
    symbols       TEXT
);
CREATE INDEX IF NOT EXISTS idx_doc_pages_package ON doc_pages(package);
CREATE INDEX IF NOT EXISTS idx_doc_pages_module ON doc_pages(module);

CREATE TABLE IF NOT EXISTS postings (
    term      TEXT NOT NULL,
    page_id   INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    tf        INTEGER NOT NULL,
    positions TEXT NOT NULL,
    PRIMARY KEY (term, page_id)
) WITHOUT ROWID;
"""

STRUCTURED_TABLES = {
    COURSE: ("courses", ("course_code", "title", "instructor", "credit_hours")),
    FACULTY: ("faculty", ("name", "position", "department", "email", "research_keywords")),
    LAB: ("labs", ("name", "pi", "department")),
    **DOC_TABLES,
}
TABLES = tuple(dict.fromkeys(table for table, _ in STRUCTURED_TABLES.values()))


class Storage:
    def __init__(self, path: str | Path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def commit(self) -> None:
        self.conn.commit()

    # --- writes ---------------------------------------------------------------

    def upsert_page(
        self,
        url: str,
        host: str,
        depth: int,
        status: int | None,
        content_type: str | None = None,
        title: str | None = None,
        description: str | None = None,
        label: str | None = None,
        label_score: float | None = None,
        content_hash: str | None = None,
        last_modified: float | None = None,
        fields: dict | None = None,
    ) -> int:
        duplicate_of = None
        if content_hash:
            row = self.conn.execute(
                "SELECT id FROM pages WHERE content_hash = ? AND url != ? AND duplicate_of IS NULL",
                (content_hash, url),
            ).fetchone()
            duplicate_of = row["id"] if row else None
        self.conn.execute(
            """
            INSERT INTO pages (url, host, depth, status, content_type, title, description, label,
                               label_score, content_hash, duplicate_of, last_modified, fetched_at,
                               fields_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                depth=excluded.depth, status=excluded.status, content_type=excluded.content_type,
                title=excluded.title, description=excluded.description, label=excluded.label,
                label_score=excluded.label_score, content_hash=excluded.content_hash,
                duplicate_of=excluded.duplicate_of, last_modified=excluded.last_modified,
                fetched_at=excluded.fetched_at, fields_json=excluded.fields_json
            """,
            (
                url,
                host,
                depth,
                status,
                content_type,
                title,
                description,
                label,
                label_score,
                content_hash,
                duplicate_of,
                last_modified,
                time.time(),
                json.dumps(fields) if fields else None,
            ),
        )
        return self.conn.execute("SELECT id FROM pages WHERE url = ?", (url,)).fetchone()["id"]

    def replace_links(self, page_id: int, links: Iterable[tuple[str, str]]) -> None:
        self.conn.execute("DELETE FROM links WHERE src_id = ?", (page_id,))
        self.conn.executemany(
            "INSERT OR IGNORE INTO links (src_id, dst_url, anchor) VALUES (?, ?, ?)",
            ((page_id, url, anchor) for url, anchor in links),
        )

    def replace_structured(self, page_id: int, label: str, fields: dict) -> None:
        for table in TABLES:
            self.conn.execute(f"DELETE FROM {table} WHERE page_id = ?", (page_id,))
        if label not in STRUCTURED_TABLES:
            return
        table, columns = STRUCTURED_TABLES[label]
        values = [json.dumps(v) if isinstance(v := fields.get(c), list) else v for c in columns]
        placeholders = ", ".join("?" * (len(columns) + 1))
        self.conn.execute(
            f"INSERT INTO {table} (page_id, {', '.join(columns)}) VALUES ({placeholders})",
            (page_id, *values),
        )

    def replace_postings(self, page_id: int, positions: dict[str, list[int]]) -> None:
        self.conn.execute("DELETE FROM postings WHERE page_id = ?", (page_id,))
        self.conn.executemany(
            "INSERT INTO postings (term, page_id, tf, positions) VALUES (?, ?, ?, ?)",
            ((t, page_id, len(p), json.dumps(p)) for t, p in positions.items()),
        )
        doc_length = sum(len(p) for p in positions.values())
        self.conn.execute("UPDATE pages SET doc_length = ? WHERE id = ?", (doc_length, page_id))

    # --- reads ----------------------------------------------------------------

    def postings(self, terms: Iterable[str]) -> list[sqlite3.Row]:
        terms = list(dict.fromkeys(terms))
        if not terms:
            return []
        marks = ", ".join("?" * len(terms))
        return self.conn.execute(
            f"""
            SELECT p.term, p.page_id, p.tf FROM postings p
            JOIN pages g ON g.id = p.page_id
            WHERE p.term IN ({marks}) AND g.duplicate_of IS NULL
            """,
            terms,
        ).fetchall()

    def document_frequency(self, terms: Iterable[str]) -> dict[str, int]:
        terms = list(dict.fromkeys(terms))
        if not terms:
            return {}
        marks = ", ".join("?" * len(terms))
        rows = self.conn.execute(
            f"SELECT term, COUNT(*) AS df FROM postings WHERE term IN ({marks}) GROUP BY term",
            terms,
        )
        return {r["term"]: r["df"] for r in rows}

    def indexed_count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM pages WHERE doc_length > 0 AND duplicate_of IS NULL"
        ).fetchone()[0]

    def pages_by_id(self, ids: Iterable[int]) -> dict[int, sqlite3.Row]:
        ids = list(ids)
        if not ids:
            return {}
        marks = ", ".join("?" * len(ids))
        rows = self.conn.execute(f"SELECT * FROM pages WHERE id IN ({marks})", ids)
        return {r["id"]: r for r in rows}

    def structured_rows(self, label: str) -> list[sqlite3.Row]:
        table, _ = STRUCTURED_TABLES[label]
        return self.conn.execute(f"SELECT * FROM {table}").fetchall()

    def label_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT COALESCE(label, 'UNFETCHED') AS label, COUNT(*) AS n FROM pages GROUP BY 1"
        )
        return {r["label"]: r["n"] for r in rows}

    def summary(self) -> dict:
        q = self.conn.execute
        return {
            "pages": q("SELECT COUNT(*) FROM pages").fetchone()[0],
            "indexed": self.indexed_count(),
            "duplicates": q("SELECT COUNT(*) FROM pages WHERE duplicate_of IS NOT NULL").fetchone()[
                0
            ],
            "hosts": q("SELECT COUNT(DISTINCT host) FROM pages").fetchone()[0],
            "links": q("SELECT COUNT(*) FROM links").fetchone()[0],
            "terms": q("SELECT COUNT(DISTINCT term) FROM postings").fetchone()[0],
            "labels": self.label_counts(),
        }

    def in_degree(self, url: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM links WHERE dst_url = ?", (url,)).fetchone()[
            0
        ]

    def iter_pages(self) -> Iterator[dict]:
        for row in self.conn.execute("SELECT * FROM pages ORDER BY id"):
            page = dict(row)
            page["fields"] = json.loads(page.pop("fields_json") or "{}")
            yield page
