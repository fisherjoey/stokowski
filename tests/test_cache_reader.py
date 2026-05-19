import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from stokowski.cache_reader import CacheReader


ISO_NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")
ISO_OLD = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")


@pytest.fixture
def cache_db(tmp_path):
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE issue (
            id TEXT PRIMARY KEY, identifier TEXT, title TEXT,
            state_id TEXT, state_name TEXT, project_id TEXT,
            labels_json TEXT, assignee_id TEXT,
            updated_at TEXT, cached_at TEXT
        );
        CREATE TABLE comment (
            id TEXT PRIMARY KEY, issue_id TEXT, body TEXT, author_id TEXT,
            created_at TEXT, updated_at TEXT, cached_at TEXT
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.commit()
    conn.close()
    return db


class TestFresh:
    def test_fresh_when_meta_recent(self, cache_db):
        conn = sqlite3.connect(cache_db)
        conn.execute("INSERT INTO meta VALUES ('last_reconcile_at', ?)", (ISO_NOW,))
        conn.execute("INSERT INTO meta VALUES ('last_webhook_at', ?)", (ISO_NOW,))
        conn.commit()
        conn.close()
        reader = CacheReader(cache_db)
        assert reader.is_fresh() is True

    def test_stale_when_reconcile_old(self, cache_db):
        conn = sqlite3.connect(cache_db)
        conn.execute("INSERT INTO meta VALUES ('last_reconcile_at', ?)", (ISO_OLD,))
        conn.commit()
        conn.close()
        reader = CacheReader(cache_db)
        assert reader.is_fresh() is False

    def test_stale_when_meta_missing(self, cache_db):
        reader = CacheReader(cache_db)
        assert reader.is_fresh() is False

    def test_stale_when_db_missing(self, tmp_path):
        reader = CacheReader(tmp_path / "nonexistent.db")
        assert reader.is_fresh() is False


class TestRead:
    def test_get_issues_by_state(self, cache_db):
        conn = sqlite3.connect(cache_db)
        for ident, sid in (("SYN-1", "s1"), ("SYN-2", "s2"), ("SYN-3", "s1")):
            conn.execute(
                "INSERT INTO issue VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ident, ident, "t", sid, "name", "p1", "[]", None, ISO_NOW, ISO_NOW),
            )
        conn.commit()
        conn.close()
        reader = CacheReader(cache_db)
        rows = reader.get_issues_by_state("p1", ["s1"])
        assert {r["identifier"] for r in rows} == {"SYN-1", "SYN-3"}

    def test_get_comments_for_issue(self, cache_db):
        conn = sqlite3.connect(cache_db)
        for cid in ("c1", "c2"):
            conn.execute(
                "INSERT INTO comment VALUES (?,?,?,?,?,?,?)",
                (cid, "u1", cid, "a1", ISO_NOW, ISO_NOW, ISO_NOW),
            )
        conn.commit()
        conn.close()
        reader = CacheReader(cache_db)
        rows = reader.get_comments_for_issue("u1")
        assert len(rows) == 2
