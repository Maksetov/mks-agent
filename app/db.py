"""SQLite storage. One row per draft; only one live draft per date."""
import json
import sqlite3
from contextlib import contextmanager

from .settings import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_date TEXT NOT NULL,            -- YYYY-MM-DD (Tashkent)
    skill TEXT NOT NULL,
    format TEXT NOT NULL,
    topic TEXT,
    payload TEXT NOT NULL,              -- JSON {topic, messages}
    issues TEXT NOT NULL DEFAULT '[]',  -- checker issues still open (JSON list)
    status TEXT NOT NULL DEFAULT 'pending',
        -- pending | approved | rejected | replaced | published | skipped | failed
    channel_msg_ids TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_drafts_date ON drafts(post_date);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

LIVE = ("pending", "approved", "published")


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def _row(r):
    if r is None:
        return None
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    d["issues"] = json.loads(d["issues"])
    return d


def add_draft(post_date, skill, fmt, payload, issues):
    with conn() as c:
        # a new draft replaces any unpublished live draft for the same date
        c.execute(
            "UPDATE drafts SET status='replaced', updated_at=datetime('now') "
            "WHERE post_date=? AND status IN ('pending','approved')",
            (post_date,),
        )
        cur = c.execute(
            "INSERT INTO drafts (post_date, skill, format, topic, payload, issues) VALUES (?,?,?,?,?,?)",
            (post_date, skill, fmt, payload.get("topic"), json.dumps(payload, ensure_ascii=False),
             json.dumps(issues, ensure_ascii=False)),
        )
        return cur.lastrowid


def update_payload(draft_id, payload, issues):
    with conn() as c:
        c.execute(
            "UPDATE drafts SET payload=?, issues=?, topic=?, status='pending', updated_at=datetime('now') WHERE id=?",
            (json.dumps(payload, ensure_ascii=False), json.dumps(issues, ensure_ascii=False),
             payload.get("topic"), draft_id),
        )


def set_status(draft_id, status, channel_msg_ids=None):
    with conn() as c:
        if channel_msg_ids is None:
            c.execute("UPDATE drafts SET status=?, updated_at=datetime('now') WHERE id=?", (status, draft_id))
        else:
            c.execute(
                "UPDATE drafts SET status=?, channel_msg_ids=?, updated_at=datetime('now') WHERE id=?",
                (status, json.dumps(channel_msg_ids), draft_id),
            )


def get(draft_id):
    with conn() as c:
        return _row(c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone())


def live_for_date(post_date):
    with conn() as c:
        return _row(c.execute(
            "SELECT * FROM drafts WHERE post_date=? AND status IN ('pending','approved','published') "
            "ORDER BY id DESC LIMIT 1", (post_date,),
        ).fetchone())


def recent_formats(skill, since_date):
    """{format: last_used_date} for published/approved drafts of this skill since since_date."""
    with conn() as c:
        rows = c.execute(
            "SELECT format, MAX(post_date) d FROM drafts WHERE skill=? AND post_date>=? "
            "AND status IN ('approved','published') GROUP BY format", (skill, since_date),
        ).fetchall()
    return {r["format"]: r["d"] for r in rows}


def recent_topics(limit):
    with conn() as c:
        rows = c.execute(
            "SELECT skill, topic FROM drafts WHERE topic IS NOT NULL AND status IN ('approved','published') "
            "ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
    return [f"{r['skill']}: {r['topic']}" for r in rows]


def kv_get(key, default=None):
    with conn() as c:
        r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def kv_set(key, value):
    with conn() as c:
        c.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, None if value is None else str(value)))
