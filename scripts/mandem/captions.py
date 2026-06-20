#!/usr/bin/env python3
# scripts/mandem/captions.py
# Publish-caption resolution + recaption-without-restylize.
#
# Two concerns, one home:
#   - resolve_publish_caption: the single source of truth for what text actually
#     publishes (base caption + CC attribution for wikimedia/pexels - the only
#     carve-out from the no-outlet-credits rule). Was duplicated across
#     stylize_async / publish_raw_to_queue / publish_to_instagram.
#   - recaption_styled_draft: change the caption on an ALREADY-STYLIZED draft
#     WITHOUT re-running the gen. Re-stylizing produces a brand-new image (the
#     overlay is baked in by gpt-image-2 and the gen is non-deterministic), which
#     is exactly how an approved graphic got swapped for a different one. This
#     reuses the approved image and only rewrites the caption.

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import _env

DB_PATH = _env.data_dir() / "db.sqlite"

# Sources whose license REQUIRES attribution in the public caption.
_CC_SOURCES = ("wikimedia", "pexels")


def resolve_publish_caption(draft: dict, base_caption: str) -> str:
    """The caption that actually ships: base text + CC attribution appended for
    wikimedia/pexels sources only. Idempotent — never double-appends the credit."""
    cap = (base_caption or "").strip()
    attr = (draft.get("image_attribution") or "").strip()
    src = (draft.get("image_source") or "").lower()
    if attr and src.startswith(_CC_SOURCES) and attr not in cap:
        cap = f"{cap}\n\n{attr}".strip()
    return cap


def _connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def recaption_styled_draft(draft_id: int, new_caption: str,
                           *, db_path: Path | str = DB_PATH) -> dict:
    """Change the caption on an already-stylized draft WITHOUT regenerating the image.

    Rewrites the queued caption.md and the draft's edit_text; leaves styled_path,
    queued_path, status and the image file itself untouched. Use this whenever the
    image is already approved and only the caption changes — calling stylize_image
    again would generate a different graphic.

    Returns {ok, caption, queued_path} or {ok: False, error}."""
    if not (new_caption or "").strip():
        return {"ok": False, "error": "empty caption — refusing to blank the post"}

    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM post_drafts WHERE id = ?", (int(draft_id),)
        ).fetchone()
    if not row:
        return {"ok": False, "error": "draft not found"}
    draft = dict(row)

    if not draft.get("styled_path") or not draft.get("queued_path"):
        return {"ok": False,
                "error": "draft not stylized yet — use stylize_image, not recaption"}

    # Only pre-publish drafts may be recaptioned. A 'posting' draft has been claimed
    # by an in-flight publish that reads caption.md moments later — rewriting it here
    # could ship a caption other than the one approved. 'posted'/'post_unknown' are
    # already live; mutating the local record would just diverge it from reality.
    status = (draft.get("status") or "").lower()
    if status in ("posting", "posted", "post_unknown"):
        return {"ok": False,
                "error": f"draft is '{status}' — already live or publishing; recaption refused"}

    caption = resolve_publish_caption(draft, new_caption)

    qd = Path(draft["queued_path"])
    if qd.exists():
        (qd / "caption.md").write_text(caption, encoding="utf-8")

    # Store the raw (un-attributed) caption as edit_text; caption.md (with the
    # credit) stays authoritative at publish time.
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE post_drafts SET edit_text = ? WHERE id = ?",
            (new_caption, int(draft_id)),
        )
        conn.commit()

    return {"ok": True, "caption": caption, "queued_path": str(qd)}
