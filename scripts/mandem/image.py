#!/usr/bin/env python3
# scripts/mandem/image.py
# Three text-to-image / search backends behind one dispatcher: gpt-image-2 / gemini-3-pro / pexels.
# Used by the generate_brand_image MCP tool + Pexels search — NOT the stylise engine
# (that's Seedream/fal in falimg.py). Pure stdlib (urllib) — no httpx/openai SDK needed.
#
# Usage:
#   python3 -m scripts.mandem.image test --source gpt_image --prompt "Arsenal celebrating a late winner"
#   python3 -m scripts.mandem.image test --source imagen    --prompt "Liverpool fans roaring at Anfield"
#   python3 -m scripts.mandem.image test --source pexels    --query  "Arsenal stadium crowd"

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import _env

DEFAULT_OUT_DIR = _env.data_dir() / "images"
DEFAULT_UA = "mandem-fc-agent/0.1 (football-social-agent)"

# Hard-coded so the agent's image style is consistent across drafts. Tunable here, not in the soul.
STYLE_PREAMBLE = (
    "stylised football illustration, muted palette, cinematic light, "
    "high contrast, single subject, no text overlays"
)

# Aspect → provider-specific hint mapping.
_SIZE_GPT_IMAGE = {"1:1": "1024x1024", "16:9": "1536x1024", "4:5": "1024x1536"}
_GEMINI_ASPECT_HINT = {
    "1:1": "square 1:1 aspect ratio",
    "16:9": "16:9 landscape orientation",
    "4:5": "4:5 portrait orientation",
}


@dataclass
class ImageBrief:
    """What to depict + how to find it."""
    prompt: str               # imperative description, used for gen backends
    query: str = ""           # search terms, used for pexels (falls back to prompt)
    aspect: str = "1:1"       # "1:1" | "16:9" | "4:5"
    alt: str = ""             # alt text for accessibility


@dataclass
class ImageResult:
    path: Path
    source: str               # "gpt_image" | "imagen" | "pexels"
    cost_usd: float           # rough estimate
    meta: dict = field(default_factory=dict)


# ---------- backend: OpenAI gpt-image-2 (text-to-image) ----------

def _gpt_image(brief: ImageBrief, out_dir: Path) -> ImageResult:
    # MANDEM_OPENAI_KEY takes precedence so image gen can use a real OpenAI key
    # even when OPENAI_API_KEY is aliased to a different provider for the brain
    # (e.g., GLM via z.ai's OpenAI-compatible endpoint). Falls back to the
    # standard OPENAI_API_KEY when the dedicated var isn't set.
    _env.load()
    api_key = os.environ.get("MANDEM_OPENAI_KEY") or _env.require("OPENAI_API_KEY")
    size = _SIZE_GPT_IMAGE.get(brief.aspect, "1024x1024")
    full_prompt = f"{STYLE_PREAMBLE}. {brief.prompt}"

    payload = {
        "model": "gpt-image-2",
        "prompt": full_prompt,
        "size": size,
        "n": 1,
        "quality": "medium",
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_UA,
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    b64 = data["data"][0]["b64_json"]
    out = out_dir / f"gpt_{int(time.time())}.png"
    out.write_bytes(base64.b64decode(b64))
    return ImageResult(
        path=out,
        source="gpt_image",
        cost_usd=0.04,
        meta={"model": "gpt-image-2", "size": size, "quality": "medium", "ref": False},
    )


# ---------- backend: Gemini Image Pro 3 (gemini-3-pro-image-preview) ----------

def _gemini_image(brief: ImageBrief, out_dir: Path) -> ImageResult:
    """Uses gemini-3-pro-image-preview via generateContent — text-to-image for the
    generate_brand_image fallback."""
    api_key = _env.require("GEMINI_API_KEY")
    model = "gemini-3-pro-image-preview"
    aspect_hint = _GEMINI_ASPECT_HINT.get(brief.aspect, _GEMINI_ASPECT_HINT["1:1"])
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={urllib.parse.quote(api_key)}"
    )
    text_part = {"text": f"{STYLE_PREAMBLE}, {aspect_hint}. {brief.prompt}"}
    parts: list[dict] = [text_part]
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": DEFAULT_UA},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            b64 = inline["data"]
            mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
            ext = "png" if "png" in mime else ("webp" if "webp" in mime else "jpg")
            out = out_dir / f"gemini_{int(time.time())}.{ext}"
            out.write_bytes(base64.b64decode(b64))
            return ImageResult(
                path=out,
                source="gemini",
                cost_usd=0.04,
                meta={"model": model, "aspect": brief.aspect, "mime": mime},
            )
    raise RuntimeError(f"Gemini response missing image part: {json.dumps(data)[:400]}")


# ---------- backend: Pexels search ----------

def _pexels(brief: ImageBrief, out_dir: Path) -> ImageResult:
    api_key = _env.require("PEXELS_API_KEY")
    query = brief.query or brief.prompt
    search_url = (
        "https://api.pexels.com/v1/search?"
        + urllib.parse.urlencode({"query": query, "per_page": "5", "orientation": "landscape" if brief.aspect == "16:9" else "square"})
    )
    req = urllib.request.Request(
        search_url,
        headers={"Authorization": api_key, "User-Agent": DEFAULT_UA},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    photos = data.get("photos") or []
    if not photos:
        raise RuntimeError(f"Pexels: no results for query={query!r}")
    photo = photos[0]
    img_url = photo["src"].get("large2x") or photo["src"]["large"]
    img_req = urllib.request.Request(img_url, headers={"User-Agent": DEFAULT_UA})
    with urllib.request.urlopen(img_req, timeout=30) as r:
        img_bytes = r.read()
    out = out_dir / f"pexels_{photo['id']}.jpg"
    out.write_bytes(img_bytes)
    return ImageResult(
        path=out,
        source="pexels",
        cost_usd=0.0,
        meta={
            "photo_id": photo["id"],
            "photographer": photo["photographer"],
            "photographer_url": photo["photographer_url"],
            "pexels_url": photo["url"],
            "query": query,
        },
    )


# ---------- aspect normalisation ----------

# IG-native portrait. Every published photo is normalised to this exact size so a
# post can never ship square / wrong-ratio again. When a source is taller than 4:5
# the excess height is trimmed off the BOTTOM — the Seedream headline sits in the TOP
# third and a raw portrait's face is in the upper region, so the top must be kept.
PORTRAIT_45 = (1080, 1350)


def normalize_to_45(src_path: Path | str, out_path: Path | str | None = None,
                    size: tuple[int, int] = PORTRAIT_45) -> Path:
    """Force an image to exactly `size` (default 1080x1350, 4:5) AND an IG-safe JPEG.

    EXIF orientation is applied first (phone/news/stock JPEGs often carry an
    orientation flag) so the crop can't go sideways. Crop-to-fill: if the source is
    wider than the target ratio, centre-crop the width; if taller (or equal), trim the
    excess height off the BOTTOM so the TOP band — the Seedream headline / a portrait's
    face — is always kept. Then resize and write a JPEG. IG's image_url publish rejects
    WebP, so we always emit `.jpg`; if the source had a different extension (e.g. a
    .webp) the original is removed. Returns the JPEG path (may differ from src). The
    save happens after the source file handle is closed, so an in-place rewrite can't
    truncate the source."""
    from PIL import Image, ImageOps  # local import keeps stdlib startup cheap

    src_path = Path(src_path)
    out_path = Path(out_path) if out_path else src_path.with_suffix(".jpg")
    tw, th = size
    target = tw / th
    with Image.open(src_path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")   # honour orientation flag
        w, h = im.size
        if w / h > target:
            new_w = round(h * target)            # too wide → centre-crop width
            x0 = (w - new_w) // 2
            box = (x0, 0, x0 + new_w, h)
        else:
            new_h = round(w / target)            # too tall → keep the TOP band
            box = (0, 0, w, new_h)               # trim the excess height off the bottom
        out_img = im.crop(box).resize((tw, th), Image.LANCZOS)
    out_img.save(out_path, "JPEG", quality=92)
    if out_path != src_path and src_path.exists():
        src_path.unlink()                        # drop the stale non-jpg original
    return out_path


# ---------- public API ----------

def make_image(brief: ImageBrief, source: str, out_dir: Path | None = None) -> ImageResult:
    """Dispatch to the chosen backend. Returns ImageResult with path on disk + metadata."""
    _env.load()
    out_dir = out_dir or DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    if source == "gpt_image":
        return _gpt_image(brief, out_dir)
    if source in ("gemini", "imagen"):  # "imagen" kept as alias for backwards compat
        return _gemini_image(brief, out_dir)
    if source == "pexels":
        return _pexels(brief, out_dir)
    raise ValueError(f"unknown image source: {source!r} (expected gpt_image|gemini|pexels)")


# ---------- CLI ----------

def _cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="image")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("test", help="generate one image and print path + meta")
    t.add_argument("--source", required=True, choices=["gpt_image", "gemini", "imagen", "pexels"])
    t.add_argument("--prompt", default="A dramatic late winner being celebrated by Arsenal players in front of the home end")
    t.add_argument("--query", default="")
    t.add_argument("--aspect", default="1:1", choices=["1:1", "16:9", "4:5"])
    args = p.parse_args(argv)

    if args.cmd == "test":
        brief = ImageBrief(prompt=args.prompt, query=args.query, aspect=args.aspect)
        result = make_image(brief, source=args.source)
        print(f"  source: {result.source}")
        print(f"  path:   {result.path}")
        print(f"  cost:   ~${result.cost_usd:.2f}")
        print(f"  meta:   {json.dumps(result.meta)}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
