#!/usr/bin/env python3
"""
mandem_db.py — sqlite helper for Mandem FC's standalone database at
<MANDEM_DATA_DIR>/db.sqlite.

Usage:
  python3 mandem_db.py footy init
  python3 mandem_db.py query "SELECT id, kind, status FROM post_drafts ORDER BY id DESC LIMIT 5"

Only Mandem-specific schema + utilities remain.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path


def _data_dir() -> Path:
    raw = os.environ.get("MANDEM_DATA_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "share" / "mandem-fc"


DB_PATH = _data_dir() / "db.sqlite"


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def cmd_footy(args):
    """Mandem FC tables: ft_events, post_drafts, news_items, reddit_radar,
    telegram_state, stylize_jobs. Idempotent; safe to re-run."""
    db = get_db()
    if not args or args[0] == "init":
        db.executescript("""
            CREATE TABLE IF NOT EXISTS ft_events (
              id INTEGER PRIMARY KEY,
              ts TEXT NOT NULL DEFAULT (datetime('now')),
              fixture_id INTEGER NOT NULL UNIQUE,
              league TEXT NOT NULL,
              home TEXT NOT NULL,
              away TEXT NOT NULL,
              score_home INTEGER NOT NULL,
              score_away INTEGER NOT NULL,
              importance INTEGER NOT NULL,
              scorers_json TEXT,
              red_cards_json TEXT,
              ended_at_utc TEXT NOT NULL,
              used INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_ft_events_used ON ft_events(used);
            CREATE INDEX IF NOT EXISTS idx_ft_events_ended ON ft_events(ended_at_utc);

            CREATE TABLE IF NOT EXISTS post_drafts (
              id INTEGER PRIMARY KEY,
              ts TEXT NOT NULL DEFAULT (datetime('now')),
              event_id INTEGER NOT NULL REFERENCES ft_events(id),
              caption TEXT NOT NULL,
              image_path TEXT,
              image_source TEXT,
              status TEXT NOT NULL DEFAULT 'pending',
              edit_text TEXT,
              approved_at TEXT,
              styled_path TEXT,                  -- post-stylization image path
              queued_path TEXT,                  -- queue dir path
              tg_chat_id TEXT,                   -- approval DM chat id
              tg_message_id INTEGER,             -- msg_id of the photo we sent for approval
              awaiting_reply_msg_id INTEGER,     -- msg_id of bot's "send rewrite as a reply" prompt (set on ✏)
              error TEXT                         -- last error if a stylization step failed
            );
            CREATE INDEX IF NOT EXISTS idx_post_drafts_status ON post_drafts(status);
            CREATE INDEX IF NOT EXISTS idx_post_drafts_event ON post_drafts(event_id);
            CREATE INDEX IF NOT EXISTS idx_post_drafts_awaiting ON post_drafts(awaiting_reply_msg_id);

            CREATE TABLE IF NOT EXISTS telegram_state (
              bot_username TEXT PRIMARY KEY,
              last_update_id INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS news_items (
              id INTEGER PRIMARY KEY,
              ts TEXT NOT NULL DEFAULT (datetime('now')),
              source TEXT NOT NULL,         -- e.g. 'rss:bbc-football', 'rss:guardian', 'reddit:soccer'
              source_name TEXT,             -- human-readable
              url_hash TEXT NOT NULL UNIQUE,-- sha256 of canonical url; dedupes
              url TEXT NOT NULL,
              title TEXT NOT NULL,
              summary TEXT,
              published TEXT,               -- pubDate from feed
              league TEXT,                  -- best-guess tag
              heat_score INTEGER NOT NULL DEFAULT 0,
              used INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_news_items_used ON news_items(used);
            CREATE INDEX IF NOT EXISTS idx_news_items_ts ON news_items(ts);

            CREATE TABLE IF NOT EXISTS reddit_radar (
              id INTEGER PRIMARY KEY,
              ts TEXT NOT NULL DEFAULT (datetime('now')),
              permalink TEXT NOT NULL UNIQUE,
              title TEXT NOT NULL,
              score INTEGER NOT NULL DEFAULT 0,
              num_comments INTEGER NOT NULL DEFAULT 0,
              flair TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_reddit_radar_ts ON reddit_radar(ts);

            -- status: 'queued' | 'running' | 'done' | 'failed'
            CREATE TABLE IF NOT EXISTS stylize_jobs (
              job_id TEXT PRIMARY KEY,
              draft_id INTEGER NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL DEFAULT (datetime('now')),
              finished_at TEXT,
              queue_dir TEXT,
              styled_path TEXT,
              caption_path TEXT,
              backend TEXT,
              cost_usd REAL,
              error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_stylize_jobs_draft ON stylize_jobs(draft_id);
            CREATE INDEX IF NOT EXISTS idx_stylize_jobs_status ON stylize_jobs(status);

            -- kind ∈ {'ft','preview','lineup','news','goal'}; subject_id meaning depends on kind:
            --   ft      → ft_events.id  (also mirrored in event_id for back-compat)
            --   preview → fixture_id from API-Football (no local row)
            --   lineup  → fixture_id from API-Football
            --   news    → news_items.id
            --   goal    → ft_events.id (or live fixture_id during a match)
        """)
        for ddl in [
            "ALTER TABLE post_drafts ADD COLUMN kind TEXT NOT NULL DEFAULT 'ft'",
            "ALTER TABLE post_drafts ADD COLUMN subject_id INTEGER",
            "ALTER TABLE post_drafts ADD COLUMN image_attribution TEXT",
            # Instagram publishing (Part B): set when a post actually goes live on IG.
            "ALTER TABLE post_drafts ADD COLUMN ig_media_id TEXT",
            "ALTER TABLE post_drafts ADD COLUMN ig_permalink TEXT",
            "ALTER TABLE post_drafts ADD COLUMN posted_at TEXT",
        ]:
            try:
                db.execute(ddl)
            except Exception:
                pass
        db.commit()
        print(f"  schema ready at {DB_PATH}")
    db.close()


def cmd_query(args):
    db = get_db()
    sql = " ".join(args)
    rows = db.execute(sql).fetchall()
    for r in rows:
        print(json.dumps(dict(r), default=str))
    db.close()


def cmd_cleanup(args):
    """Purge old operational rows so the SQLite file stays small.
    Defaults: terminal drafts (posted to IG, or lineup/raw 'published') > 90 days,
    used news items > 30 days, terminal stylize_jobs > 90 days, reddit_radar > 14 days.
    Drafts still mid-flight (pending / awaiting_post_confirm / posting / post_unknown) and
    failed/abandoned rows are kept. Run with --dry-run to count without deleting.
    Run with --keep-days-drafts=N etc. to override defaults."""
    dry = "--dry-run" in args
    opts = {
        "drafts": 90,
        "news": 30,
        "stylize": 90,
        "reddit": 14,
    }
    for a in args:
        for k in opts:
            prefix = f"--keep-days-{k}="
            if a.startswith(prefix):
                try:
                    opts[k] = int(a[len(prefix):])
                except ValueError:
                    print(f"  bad value for {prefix}; using default {opts[k]}")
    db = get_db()
    queries = [
        ("post_drafts (posted/terminal)", opts["drafts"],
         "FROM post_drafts WHERE status IN ('posted','published') "
         "AND COALESCE(posted_at, approved_at, ts) < datetime('now', ?)"),
        ("news_items (used)", opts["news"],
         "FROM news_items WHERE used=1 AND ts < datetime('now', ?)"),
        ("stylize_jobs (terminal)", opts["stylize"],
         "FROM stylize_jobs WHERE status IN ('done','failed') AND started_at < datetime('now', ?)"),
        ("reddit_radar", opts["reddit"],
         "FROM reddit_radar WHERE ts < datetime('now', ?)"),
    ]
    total = 0
    for label, days, where in queries:
        cutoff = f"-{days} days"
        cnt = db.execute(f"SELECT COUNT(*) {where}", (cutoff,)).fetchone()[0]
        action = "would delete" if dry else "deleted"
        if not dry and cnt:
            db.execute(f"DELETE {where}", (cutoff,))
        print(f"  {label}: {action} {cnt} rows older than {days}d")
        total += cnt
    if not dry:
        db.commit()
        db.isolation_level = None  # VACUUM must run outside a transaction
        db.execute("VACUUM")
    print(f"  total: {total} rows{' (dry-run)' if dry else ''}")
    db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    commands = {
        "footy": cmd_footy,
        "query": cmd_query,
        "cleanup": cmd_cleanup,
    }

    if cmd in commands:
        commands[cmd](args)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
