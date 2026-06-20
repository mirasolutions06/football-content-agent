#!/usr/bin/env python3
# scripts/mandem/stylize.py
# Post-approval AI stylization: raw real photo + caption hot-take overlay → IG-ready graphic.
#
# Pipeline:
#   1. make_overlay_phrase / overlay_from_caption → ONE dramatic clickbait-pundit word
#      (the caption's first line if it's a clean slug, else gemini-2.5-flash, banlist-filtered)
#   2. stylize_for_publish  → Seedream v4 (fal) edits the REAL player photo:
#        a. aura-sr upscale the source if it's small, then Seedream v4 ref-edit
#           (identity-safe, safety_checker off, orange headline up top)
#        b. same_subject identity QC vs the approved photo
#        c. composite_overlay — deterministic Pillow overlay on the REAL photo if fal
#           fails or the QC says the player mutated (identity can't change)
#
# This is invoked from a worker thread by stylize_async.submit_job — the MCP
# tool returns a job_id in <1s and this ~30-90s pipeline runs in the background.
#
# Output: <MANDEM_DATA_DIR>/queue/<YYYY-MM-DD-HHMMSS>/{image.jpg, caption.md, meta.json}

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from . import _env
from . import falimg
from .image import ImageResult, normalize_to_45
from .vision_check import same_subject

QUEUE_ROOT = _env.data_dir() / "queue"

# Overlay phrase generation. Was on the legacy moonshot-v1-8k (weak/old → occasional
# nonsense words); now gemini-2.5-flash via its OpenAI-compatible endpoint — much
# sharper at punchy English, and cheaper. URL/model/key are env-overridable so the
# operator can swap providers without editing code.
import os as _os_overlay_mod
# Default: gemini-2.5-flash via Gemini's OpenAI-compatible endpoint — much sharper at
# punchy English than the legacy moonshot-v1-8k, ~$0.0004/call. Provider-swappable via
# env (set MANDEM_OVERLAY_URL / MANDEM_OVERLAY_MODEL / MANDEM_OVERLAY_KEY — no code edit).
OVERLAY_URL = _os_overlay_mod.environ.get(
    "MANDEM_OVERLAY_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
)
OVERLAY_MODEL = _os_overlay_mod.environ.get("MANDEM_OVERLAY_MODEL", "gemini-2.5-flash")
OVERLAY_MAX_CHARS = 25


# ---------- helpers ----------

_FIRST_SENTENCE_RE = re.compile(r"^([^.!?\n]{8,160}[.!?])", re.MULTILINE)
_OVERLAY_CLEAN_RE = re.compile(r"[^A-Z0-9 \-!?]")

# Words that punch down on individual players — never as overlays. If the LLM
# returns one of these, fall back to the heuristic. Pundit-clickbait drama only.
_OVERLAY_BANLIST = {
    "FRAUD", "EMBARRASSMENT", "DISGRACE", "PATHETIC", "DESTROYED",
    "RUINED", "BROKEN", "DEMOLISHED", "USELESS", "WORTHLESS",
    "JOKE", "SHAMBLES", "TRASH", "GARBAGE",
}
_HEURISTIC_FALLBACK = "SCENES"


def _heuristic_overlay(caption: str) -> str:
    """Fallback when the LLM compression call fails — extract ONE punchy dramatic word.
    Multi-word phrases get garbled by AI image gen text rendering; single dramatic words
    render cleanly."""
    if not caption:
        return _HEURISTIC_FALLBACK
    # Prefer existing CAPS streaks (user shouted on purpose) — but skip banned words
    for m in re.finditer(r"\b[A-Z]{4,}\b", caption):
        candidate = m.group(0)
        if candidate not in _OVERLAY_BANLIST:
            return candidate[:OVERLAY_MAX_CHARS]
    # Else first strong word from the first sentence
    m = _FIRST_SENTENCE_RE.search(caption)
    head = (m.group(1) if m else caption[:80]).strip()
    skip = {"the", "and", "but", "for", "with", "that", "this", "from", "they",
            "what", "when", "where", "their", "have", "been", "were", "was"}
    words = [w for w in re.split(r"\s+", head) if len(w) > 3 and w.lower() not in skip]
    for w in words:
        up = w.upper()
        if up not in _OVERLAY_BANLIST:
            return up[:OVERLAY_MAX_CHARS]
    return _HEURISTIC_FALLBACK


_OVERLAY_SYSTEM_PROMPT = (
    "You write ONE-word overlay tags for UK football social-media graphics. "
    "Think Bleacher Report / 433 / Squawka — clickbait-pundit headline energy.\n\n"
    "CRITICAL: Pick a word that fits THIS specific story. The example list below "
    "is a TONE reference (clickbait-dramatic, never harsh) — NOT a vocabulary you "
    "must pick from. Coin a fresh word if the story calls for it.\n\n"
    "ONE WORD ONLY. All caps. Comment on the MOMENT, not the man.\n\n"
    "Tone reference (good — describes events with drama): "
    "SCENES, BIBLICAL, CHAOS, MAYHEM, CARNAGE, COOKED, BOTTLED, MASTERCLASS, "
    "CLINIC, COLLAPSE, COMEBACK, STUNNER, WONDERSTRIKE, GENERATIONAL, HISTORIC, "
    "ICONIC, EPIC, ABSURD, UNREAL, INSANE, WILD, DRAMA, THRILLER, STATEMENT, "
    "ROBBERY, HEARTBREAK, CLINICAL.\n\n"
    "Avoid (too personal — punches down on individuals): "
    "FRAUD, EMBARRASSMENT, DISGRACE, PATHETIC, DESTROYED, RUINED, BROKEN, "
    "OUTRAGE, MELTDOWN, DEMOLISHED.\n\n"
    "Worked examples — your word must clearly relate to the story's subject:\n"
    "  Story: 'Mudryk handed 4-year doping ban' → BANNED, DONE, FALLEN, OVER\n"
    "  Story: 'Arsenal 6-3 Spurs' → SCENES, CARNAGE, BIBLICAL, CHAOS\n"
    "  Story: 'Man Utd sack manager after defeat' → AXED, EXIT, GONE\n"
    "  Story: 'Saka late winner vs City' → CLUTCH, STUNNER, ICONIC\n"
    "  Story: 'VAR overrules clear penalty' → ROBBERY, SCANDAL\n"
    "  Story: '£200m bid for Bellingham reportedly rejected' → AUDACIOUS, BOLD\n\n"
    "No verbs that need objects ('TAKE' bad, 'BIBLICAL' good). "
    "Output ONLY the single word. No quotes, no punctuation, no other text."
)


def make_overlay_phrase(
    caption: str,
    event_summary: str = "",
    article_context: str = "",
) -> str:
    """Compress story context into ONE dramatic all-caps overlay word via gemini-2.5-flash.
    AI image gen renders single words reliably; multi-word phrases garble.

    All three context fields are passed when available so the model grounds
    the word in the SPECIFIC story, not just a generic banter dictionary:
      - caption          → the agent's hot-take (always present)
      - event_summary    → e.g. "Arsenal 4-3 Spurs (Premier League)" — for FT/goal/preview
      - article_context  → news article title + summary — for news drafts
    """
    if not caption.strip() and not event_summary and not article_context:
        return "MANDEM"

    user_lines = []
    if event_summary:
        user_lines.append(f"Event: {event_summary}")
    if article_context:
        user_lines.append(article_context)
    user_lines.append(f"Caption / hot-take:\n{caption}")
    user_lines.append("\nOne dramatic word (ALL CAPS, single word) that fits the SPECIFIC story above:")
    user_prompt = "\n\n".join(user_lines)

    try:
        _env.load()
        # Default key is Gemini's; MANDEM_OVERLAY_KEY overrides for a different provider.
        api_key = _os_overlay_mod.environ.get("MANDEM_OVERLAY_KEY") or _env.require("GEMINI_API_KEY")
        payload = {
            "model": OVERLAY_MODEL,
            "max_tokens": 256,   # room for gemini-2.5-flash's ~80 thinking tokens + the word
            "temperature": 0.45,
            "messages": [
                {"role": "system", "content": _OVERLAY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = urllib.request.Request(
            OVERLAY_URL,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
        # Moonshot reasoning models (kimi-k2.x) put final output in `content` AND
        # thinking in `reasoning_content`. If content is empty (e.g. truncated
        # reasoning), fall back to scraping the last all-caps token from reasoning.
        text = msg.get("content") or ""
        if not text.strip():
            rc = msg.get("reasoning_content") or ""
            tail = re.findall(r"\b[A-Z]{4,}\b", rc)
            text = tail[-1] if tail else ""
        # Clean up: strip quotes/punctuation/lowercase, cap word count + length
        text = text.strip().strip('"').strip("'").upper()
        text = _OVERLAY_CLEAN_RE.sub("", text).strip()
        words = [w for w in text.split() if w][:2]
        if not words:
            raise ValueError("empty overlay after cleanup")
        candidate = " ".join(words)[:OVERLAY_MAX_CHARS]
        # Hard banlist — re-roll via heuristic if LLM produced a personal-attack word
        if candidate in _OVERLAY_BANLIST or any(w in _OVERLAY_BANLIST for w in words):
            return _heuristic_overlay(caption)
        return candidate
    except Exception:
        return _heuristic_overlay(caption)


# The skill convention: the caption's FIRST LINE is the overlay slug (ALL-CAPS
# punchline) — the brain (GPT-5.5) already chose it with full story context. Prefer
# THAT over re-rolling a fresh word in make_overlay_phrase, so the baked graphic
# always matches the caption (no more MONARCH overlay on a ROYALTY caption).
_OVERLAY_LINE_MAX_CHARS = 28


def overlay_from_caption(caption: str) -> str | None:
    """Return the overlay slug from the caption's first line if it's a clean ALL-CAPS
    punchline (≤2 words, short, not banned); else None to fall back to make_overlay_phrase."""
    if not caption or not caption.strip():
        return None
    first = caption.strip().splitlines()[0].strip()
    if not first or len(first) > _OVERLAY_LINE_MAX_CHARS or len(first.split()) > 2:
        return None
    letters = [c for c in first if c.isalpha()]
    if not letters or any(c.islower() for c in letters):
        return None  # prose / mixed-case first line is not a slug
    cleaned = " ".join(_OVERLAY_CLEAN_RE.sub("", first).split())[:OVERLAY_MAX_CHARS]
    if not cleaned:
        return None
    words = cleaned.split()
    if cleaned in _OVERLAY_BANLIST or any(w in _OVERLAY_BANLIST for w in words):
        return None  # never bake a character-assassination word
    return cleaned


# Deterministic identity-SAFE stylise (fallback when the Seedream edit mutates a face).
# Condensed-bold first (Bleacher-Report look), then plain bold, then PIL default.
_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/urw-base35/NimbusSansNarrow-Bold.otf",   # VPS — condensed
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",             # VPS — bold sans
    "/System/Library/Fonts/Supplemental/Impact.ttf",                   # local dev
    "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf",
)


def _fit_font(word: str, target_w: int, start: int = 320):
    from PIL import ImageFont
    path = next((p for p in _FONT_CANDIDATES if Path(p).exists()), None)
    size = start
    while size > 36:
        f = ImageFont.truetype(path, size) if path else ImageFont.load_default()
        if f.getbbox(word, stroke_width=int(size * 0.05))[2] <= target_w or not path:
            return f
        size -= 6
    return ImageFont.truetype(path, 36) if path else ImageFont.load_default()


def composite_overlay(src_path: str | Path, overlay_word: str,
                      out_path: str | Path | None = None,
                      size: tuple[int, int] = (1080, 1350)) -> Path:
    """Identity-SAFE stylise: cover-fit the REAL photo to 4:5, apply a cinematic grade,
    and composite the overlay word in the lower third — all in code. The face/kit are
    the exact source pixels, so they CANNOT mutate (unlike a generative ref-edit).
    Writes a 1080x1350 JPEG. Used as the fallback when the Seedream edit changes the subject."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

    src_path = Path(src_path)
    out_path = Path(out_path) if out_path else src_path.with_name("composite.jpg")
    W, H = size
    im = ImageOps.exif_transpose(Image.open(src_path)).convert("RGB")  # honour orientation
    # cover-fit (fill WxH, centre-crop) so the subject stays large
    scale = max(W / im.width, H / im.height)
    im = im.resize((max(W, round(im.width * scale)), max(H, round(im.height * scale))), Image.LANCZOS)
    x0, y0 = (im.width - W) // 2, (im.height - H) // 2
    im = im.crop((x0, y0, x0 + W, y0 + H))
    # cinematic grade
    im = ImageEnhance.Color(im).enhance(0.80)
    im = ImageEnhance.Contrast(im).enhance(1.12)
    # vignette
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).ellipse([-W * 0.25, -H * 0.2, W * 1.25, H * 1.15], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(200))
    im = Image.composite(im, ImageEnhance.Brightness(im).enhance(0.5), vig)
    # bottom scrim for legibility
    scrim = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(scrim)
    top = int(H * 0.5)
    for y in range(top, H):
        sd.line([(0, y), (W, y)], fill=min(235, int(235 * (y - top) / (H - top))))
    im = Image.composite(Image.new("RGB", (W, H), (8, 8, 10)), im, scrim)
    # overlay word, lower third, centred
    word = (overlay_word or "MANDEM").upper().strip() or "MANDEM"
    f = _fit_font(word, int(W * 0.86))
    cx, cy = W // 2, int(H * 0.82)
    sw = max(6, int(getattr(f, "size", 40) * 0.05))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((cx, cy + 8), word, font=f, fill=(0, 0, 0, 210),
                                anchor="mm", stroke_width=sw, stroke_fill=(0, 0, 0, 210))
    im = Image.alpha_composite(im.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(12))).convert("RGB")
    ImageDraw.Draw(im).text((cx, cy), word, font=f, fill=(245, 245, 245),
                            anchor="mm", stroke_width=sw, stroke_fill=(12, 12, 14))
    im.save(out_path, "JPEG", quality=92)
    return out_path


@dataclass
class StylizeResult:
    queue_dir: Path
    image_path: Path
    caption_path: Path
    meta_path: Path
    backend: str
    cost_usd: float


def stylize_for_publish(
    *,
    draft_id: int,
    raw_image_path: str | Path,
    final_caption: str,
    event_summary: str,
    article_context: str = "",
    attribution: str | None = None,
    headline_color: str = "orange",
) -> StylizeResult:
    """Take a raw photo + final caption, generate a stylized graphic, write to queue.

    Identity-safe chain (Seedream edits the real photo; fall back if it fails/mutates):
      1. Seedream v4 (fal) ref-edit (4:5; aura-sr upscale first if the source is small)
         → same_subject identity check vs the approved photo
      2. composite_overlay — deterministic overlay on the REAL photo (identity can't
         mutate). Used when Seedream fails OR the styled output changed the player.
    gpt-image-2/Gemini are NOT used — they refuse celebrity photos or mutate identity.
    """
    raw_path = Path(raw_image_path)
    if not raw_path.exists():
        raise FileNotFoundError(f"raw image not found: {raw_path}")

    # Headline word: prefer the brain's chosen slug (caption's first line) so it matches
    # the caption; else derive one.
    overlay_phrase = overlay_from_caption(final_caption) or make_overlay_phrase(
        final_caption,
        event_summary=event_summary,
        article_context=article_context,
    )
    queue_dir = QUEUE_ROOT / time.strftime("%Y-%m-%d-%H%M%S")
    queue_dir.mkdir(parents=True, exist_ok=True)
    final_image = queue_dir / "image.jpg"

    # PRIMARY: Seedream v4 (fal) — edits the REAL player photo faithfully (identity-safe,
    # no celebrity block, safety checker off), upscales the source for sharpness, and
    # renders the locked Mandem look (orange bold headline up top, shadowed real background
    # + glow, player in focus). gpt-image-2/Gemini are NOT used — they refuse celebrity
    # photos or mutate identity. A cheap same-subject check guards against any drift, and
    # the deterministic composite is the last-resort fallback if fal fails or mutates.
    use_composite = False
    fallback_reason = None
    try:
        falimg.seedream_stylise(raw_path, overlay_phrase, final_image, color=headline_color)
        final_image = normalize_to_45(final_image)
        qc = same_subject(raw_path, final_image)
        if not qc.get("same"):
            raise RuntimeError(f"seedream changed the subject: {qc.get('reason')!r}")
        result = ImageResult(
            path=final_image, source="seedream", cost_usd=0.05,
            meta={"mode": "seedream_edit", "backend_used": "seedream",
                  "overlay_phrase": overlay_phrase, "identity_qc": qc},
        )
    except Exception as e:
        fallback_reason = str(e)[:300]
        use_composite = True

    if use_composite:
        # Identity-SAFE deterministic fallback: overlay on the REAL photo (cannot mutate).
        final_image = composite_overlay(raw_path, overlay_phrase, out_path=queue_dir / "image.jpg")
        result = ImageResult(
            path=final_image, source="composite_overlay", cost_usd=0.0,
            meta={"mode": "composite_fallback", "backend_used": "composite_overlay",
                  "overlay_phrase": overlay_phrase, "fallback_reason": fallback_reason},
        )

    # Write the caption + metadata sidecars
    caption_path = queue_dir / "caption.md"
    caption_path.write_text(final_caption, encoding="utf-8")

    meta_path = queue_dir / "meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "draft_id": draft_id,
                "backend_chosen": result.meta.get("backend_used", result.source),
                "mode": result.meta.get("mode"),
                "overlay_phrase": overlay_phrase,
                "event_summary": event_summary,
                "attribution": attribution,
                "raw_image_path": str(raw_path),
                "image": result.meta,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return StylizeResult(
        queue_dir=queue_dir,
        image_path=final_image,
        caption_path=caption_path,
        meta_path=meta_path,
        backend=result.meta.get("backend_used", result.source),
        cost_usd=result.cost_usd,
    )


# ---------- CLI ----------

def _cli(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="stylize")
    p.add_argument("--draft-id", type=int, required=True)
    p.add_argument("--raw", required=True, help="path to raw image")
    p.add_argument("--caption", required=True, help="final caption text (or @path/to/caption.md)")
    p.add_argument("--summary", required=True, help="short event summary, e.g. 'Arsenal 4-3 Tottenham (Premier League)'")
    p.add_argument("--attribution", default=None)
    args = p.parse_args(argv)
    cap = args.caption
    if cap.startswith("@"):
        cap = Path(cap[1:]).read_text()
    result = stylize_for_publish(
        draft_id=args.draft_id,
        raw_image_path=args.raw,
        final_caption=cap,
        event_summary=args.summary,
        attribution=args.attribution,
    )
    print(json.dumps({**asdict(result), "queue_dir": str(result.queue_dir),
                      "image_path": str(result.image_path),
                      "caption_path": str(result.caption_path),
                      "meta_path": str(result.meta_path)}, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
