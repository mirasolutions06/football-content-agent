#!/usr/bin/env python3
# scripts/tests/test_captions.py
# Tests for caption resolution + recaption-without-restylize (the manual-post bug fix).
# Run:  python3 scripts/tests/test_captions.py

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mandem.captions import recaption_styled_draft, resolve_publish_caption  # noqa: E402


# ---------- resolve_publish_caption (pure) ----------

def test_pexels_source_appends_attribution():
    draft = {"image_source": "pexels", "image_attribution": "Photo: Jane Doe / Pexels"}
    out = resolve_publish_caption(draft, "Cold finish.")
    assert out == "Cold finish.\n\nPhoto: Jane Doe / Pexels", out


def test_news_source_gets_no_attribution():
    draft = {"image_source": "news:custom", "image_attribution": None}
    out = resolve_publish_caption(draft, "Scenes at the Emirates.")
    assert out == "Scenes at the Emirates.", out


def test_attribution_is_idempotent():
    draft = {"image_source": "wikimedia", "image_attribution": "CC BY-SA / Wiki"}
    once = resolve_publish_caption(draft, "Big win.")
    twice = resolve_publish_caption(draft, once)  # feeding it back must not double-append
    assert twice == once, twice
    assert once.count("CC BY-SA / Wiki") == 1


# ---------- recaption_styled_draft (DB + FS) ----------

def _fixture(status="published", source="news:custom", attribution=None):
    d = Path(tempfile.mkdtemp())
    db = d / "db.sqlite"
    queue = d / "q"
    queue.mkdir()
    (queue / "caption.md").write_text("OLD CAPTION", encoding="utf-8")
    img = queue / "image.png"
    img.write_bytes(b"\x89PNG-stub-bytes")
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE post_drafts (id INTEGER PRIMARY KEY, caption TEXT, edit_text TEXT, "
        "image_source TEXT, image_attribution TEXT, styled_path TEXT, queued_path TEXT, status TEXT)"
    )
    conn.execute(
        "INSERT INTO post_drafts (id, caption, edit_text, image_source, image_attribution, "
        "styled_path, queued_path, status) VALUES (1, 'OLD CAPTION', NULL, ?, ?, ?, ?, ?)",
        (source, attribution, str(img), str(queue), status),
    )
    conn.commit()
    conn.close()
    return db, queue, img


def test_recaption_rewrites_caption_md_and_edit_text():
    db, queue, _ = _fixture()
    res = recaption_styled_draft(1, "NEW TAKE\n\nProper fuller caption here.", db_path=db)
    assert res["ok"] is True, res
    assert (queue / "caption.md").read_text() == "NEW TAKE\n\nProper fuller caption here."
    conn = sqlite3.connect(str(db))
    edit = conn.execute("SELECT edit_text FROM post_drafts WHERE id=1").fetchone()[0]
    conn.close()
    assert edit == "NEW TAKE\n\nProper fuller caption here.", edit


def test_recaption_leaves_image_and_status_untouched():
    db, queue, img = _fixture(status="published")
    before = img.read_bytes()
    recaption_styled_draft(1, "Different caption, same picture.", db_path=db)
    assert img.read_bytes() == before, "image file must not change"
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT status, styled_path, queued_path FROM post_drafts WHERE id=1").fetchone()
    conn.close()
    assert row[0] == "published", f"status changed: {row[0]}"
    assert row[1] == str(img) and row[2] == str(queue), "paths changed"


def test_recaption_reappends_attribution_for_cc_source():
    db, queue, _ = _fixture(source="pexels", attribution="Photo: A / Pexels")
    recaption_styled_draft(1, "Fresh caption.", db_path=db)
    assert (queue / "caption.md").read_text() == "Fresh caption.\n\nPhoto: A / Pexels"


def test_recaption_rejects_unstylized_draft():
    d = Path(tempfile.mkdtemp())
    db = d / "db.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE post_drafts (id INTEGER PRIMARY KEY, caption TEXT, edit_text TEXT, "
        "image_source TEXT, image_attribution TEXT, styled_path TEXT, queued_path TEXT, status TEXT)"
    )
    conn.execute("INSERT INTO post_drafts (id, status) VALUES (1, 'pending')")  # no styled_path
    conn.commit()
    conn.close()
    res = recaption_styled_draft(1, "whatever", db_path=db)
    assert res["ok"] is False and "styliz" in res["error"].lower(), res


def test_recaption_rejects_empty_caption():
    db, _, _ = _fixture()
    res = recaption_styled_draft(1, "   ", db_path=db)
    assert res["ok"] is False, res


def test_recaption_refused_on_already_posted_draft():
    """A live post must not be silently mutated locally."""
    db, queue, _ = _fixture(status="posted")
    res = recaption_styled_draft(1, "tried to change it", db_path=db)
    assert res["ok"] is False and ("posted" in res["error"].lower() or "live" in res["error"].lower()), res
    assert (queue / "caption.md").read_text() == "OLD CAPTION", "caption.md must be untouched"


def test_recaption_refused_while_publish_in_flight():
    """status='posting' = publish claimed; recaption here could ship a caption other
    than the one approved/previewed — exactly the bug class we are closing."""
    db, queue, _ = _fixture(status="posting")
    res = recaption_styled_draft(1, "race", db_path=db)
    assert res["ok"] is False, res
    assert (queue / "caption.md").read_text() == "OLD CAPTION"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
