#!/usr/bin/env python3
# scripts/mandem/falimg.py
# fal.ai image backends — the Mandem FC stylise engine.
#
# Why fal/Seedream: OpenAI gpt-image-2 and Google Gemini both REFUSE to edit photos
# of famous players (safety system), and gpt-image-2 mutated identities (Messi→Neymar)
# when it didn't refuse. ByteDance Seedream v4 (via fal) edits real-player photos
# faithfully — it's built for identity preservation — and fal exposes
# `enable_safety_checker: false`, so it doesn't block celebrities.
#
# Recipe: hi-res source -> aura-sr upscale (if small)
# → Seedream v4 edit (orange bold condensed headline up top, shadowed REAL background
# with glow + player in focus) → 4:5. SoccerForever / Bleacher-Report look.
#
# FAL_KEY is loaded from env by the process, MANDEM_ENV_FILE, or local .env.

from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import _env

FAL_RUN = "https://fal.run/"
SEEDREAM_EDIT = "fal-ai/bytedance/seedream/v4/edit"
AURA_SR = "fal-ai/aura-sr"

# Mix-by-moment headline colour: orange is the Mandem default; gold for legacy/huge
# moments, white for everyday takes. The agent can pass `color` to switch.
DEFAULT_HEADLINE_COLOUR = "orange"


def _key() -> str:
    _env.load()
    return _env.require("FAL_KEY")


# fal can blip (429 rate-limit, transient 5xx, dropped connection). Without a retry,
# any blip is swallowed by stylize_for_publish's blanket except and the post silently
# ships as the plain composite (no AI styling). The stylise worker runs off the MCP RPC
# (in a thread), so a few seconds of backoff is safe — the 120s MCP timeout doesn't apply.
_CALL_RETRIES = 3                                  # total attempts on transient errors
_CALL_RETRY_STATUSES = {429, 500, 502, 503, 504}   # retry these; 4xx (bad request) fails fast
_CALL_BACKOFF_BASE = 1.5                            # seconds, grows linearly per attempt


def _call(model: str, body: dict, timeout: int = 240) -> dict:
    data = json.dumps(body).encode()
    last_exc: Exception | None = None
    for attempt in range(1, _CALL_RETRIES + 1):
        req = urllib.request.Request(
            FAL_RUN + model,
            data=data,
            headers={"Authorization": "Key " + _key(), "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code not in _CALL_RETRY_STATUSES or attempt == _CALL_RETRIES:
                raise
        except urllib.error.URLError as e:             # connection reset / refused / DNS
            last_exc = e
            if attempt == _CALL_RETRIES:
                raise
        time.sleep(_CALL_BACKOFF_BASE * attempt)
    raise last_exc  # pragma: no cover — loop always returns or raises above


def _data_uri(b: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(b).decode()


def upscale_if_small(img_bytes: bytes, min_side: int = 1400) -> bytes:
    """4x aura-sr upscale when the short side is below min_side; else return unchanged.
    Source sharpness is the #1 quality lever — a low-res thumbnail edits to mush."""
    try:
        from PIL import Image
        w, h = Image.open(io.BytesIO(img_bytes)).size
        if min(w, h) >= min_side:
            return img_bytes
        o = _call(AURA_SR, {"image_url": _data_uri(img_bytes), "upscaling_factor": 4})
        url = (o.get("image") or (o.get("images") or [{}])[0]).get("url")
        if url:
            return urllib.request.urlopen(url, timeout=120).read()
    except Exception:
        pass  # upscale is best-effort — fall back to the original bytes
    return img_bytes


def seedream_prompt(word: str, color: str = DEFAULT_HEADLINE_COLOUR) -> str:
    """The locked Mandem stylise prompt. `word` = the overlay slug; `color` = headline colour."""
    return (
        "Premium football sports-graphic social post (SoccerForever / Bleacher Report style). "
        "Keep the player EXACTLY as in the photo: same face, same kit, do NOT change or replace "
        "the person; sharp, well-lit, in clear focus, popping forward. KEEP the original background "
        "but dark and cinematic: heavily shadowed, darkened, desaturated, softly blurred, strong "
        "vignette, subtle glow behind the player, so the real scene stays visible but recedes and "
        "the player is the focus. NOT flat black. "
        f'Headline: the single word "{word}" across the TOP third, in HEAVY ULTRA-BOLD CONDENSED '
        f"uppercase, FLAT solid {color} fill - NO drop shadow, NO 3D extrude, NO bevel, NO outline, "
        "NO gradient, NO glow on the letters. Clean heavy flat bold lettering like a Bleacher Report "
        "headline. No other text, no logo, no watermark. Crisp, high-detail, cinematic, high-contrast."
    )


def seedream_stylise(src_path: str | Path, overlay_word: str, out_path: str | Path,
                     color: str = DEFAULT_HEADLINE_COLOUR,
                     size: tuple[int, int] = (1080, 1350)) -> Path:
    """Upscale the source, Seedream v4 edit with the Mandem prompt, write to out_path.
    Raises on fal error so the caller can fall back to the deterministic composite."""
    src_path, out_path = Path(src_path), Path(out_path)
    img = upscale_if_small(src_path.read_bytes())
    o = _call(SEEDREAM_EDIT, {
        "prompt": seedream_prompt(overlay_word, color),
        "image_urls": [_data_uri(img)],
        "enable_safety_checker": False,
        "image_size": {"width": size[0], "height": size[1]},
    })
    url = o["images"][0]["url"]
    out_path.write_bytes(urllib.request.urlopen(url, timeout=120).read())
    return out_path
