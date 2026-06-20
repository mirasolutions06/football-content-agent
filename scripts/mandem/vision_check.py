#!/usr/bin/env python3
# scripts/mandem/vision_check.py
# Ask Gemini Vision: "does this image actually show {expected_subject}?"
# Used after a search hit to validate relevance before committing to a draft.
#
# Cost: ~$0.001-0.002 per check (gemini-2.5-flash, multimodal in). Negligible.
# Returns {"ok": bool, "verdict": "yes|no|unclear", "confidence": 0-1, "reason": "..."}.

from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from . import _env

# Env-overridable so the operator can swap to gemini-2.5-pro or a newer Flash
# without editing code (set MANDEM_VISION_MODEL in secrets.env).
VISION_MODEL = os.environ.get("MANDEM_VISION_MODEL", "gemini-2.5-flash")
VISION_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{VISION_MODEL}:generateContent"
)


# A confident "yes" is required to spend a paid gpt-image-2 stylize ($0.04) on an
# image. Anything below this is downgraded to "unclear" so the agent re-picks
# instead of committing — fail-closed beats burning tokens on a wrong photo.
YES_CONFIDENCE_FLOOR = 0.55


def _finalize_verdict(verdict: str, confidence: float) -> str:
    """Fail-closed normaliser: only a confident 'yes' stays 'yes'; a low-confidence
    'yes' or any unrecognised verdict becomes 'unclear' (treated as do-not-use)."""
    v = (verdict or "unclear").strip().lower()
    if v not in ("yes", "no", "unclear"):
        return "unclear"
    if v == "yes" and float(confidence or 0.0) < YES_CONFIDENCE_FLOOR:
        return "unclear"
    return v


def assess(image_path: str | Path, expected: str) -> dict:
    """Vision-check an image's relevance to a stated subject.

    expected: free-text description of what the image SHOULD contain.
              e.g. "Bukayo Saka celebrating an Arsenal goal", "Anfield stadium crowd",
              "Mikel Arteta on the touchline".

    Returns a dict the agent can act on:
      verdict: "yes" → image fits; use it.
      verdict: "no"  → wrong subject; pick a different result.
      verdict: "unclear" → ambiguous; agent decides.
    """
    p = Path(image_path)
    if not p.exists():
        return {"ok": False, "verdict": "unclear", "error": f"image not found: {p}"}

    _env.load()
    try:
        api_key = _env.require("GEMINI_API_KEY")
    except RuntimeError as e:
        # Distinct verdict so the agent knows the check didn't run — don't conflate
        # missing-capability with genuinely-ambiguous image.
        return {"ok": False, "verdict": "unavailable", "reason": "GEMINI_API_KEY missing", "error": str(e)}

    img_bytes = p.read_bytes()
    ext = p.suffix.lower().lstrip(".") or "jpg"
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp"}.get(ext, "image/jpeg")

    prompt = (
        f'You are verifying a sports photo BEFORE it gets published. The post is about: "{expected}".\n'
        f"Is the MAIN, central subject of THIS photo specifically that person/team? Be STRICT and FAIL-CLOSED:\n"
        f"- Check the kit colours, club/country badge, and any visible name or shirt number to confirm identity.\n"
        f"- Answer \"no\" if it shows a DIFFERENT player, a different club's or country's kit, a look-alike, a "
        f"generic/unidentifiable player, a crowd, or anything off-subject.\n"
        f"- Only answer \"yes\" if you are clearly confident it is exactly \"{expected}\". When in ANY doubt, answer \"no\".\n"
        f"Reply ONLY with valid JSON (no markdown, no preamble):\n"
        f'{{"verdict":"yes" | "no" | "unclear","confidence":0.0,"reason":"<one short sentence naming what you actually see>"}}'
    )

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(img_bytes).decode()}},
            ],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
            # gemini-2.5-flash uses ~80 internal "thoughts" tokens before output;
            # need headroom above the actual JSON length
            "maxOutputTokens": 500,
        },
    }
    url = f"{VISION_URL}?key={urllib.parse.quote(api_key)}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "mandem-fc-agent/0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "verdict": "unclear", "error": f"HTTP {e.code}: {e.read().decode()[:300]}"}
    except Exception as e:
        return {"ok": False, "verdict": "unclear", "error": str(e)[:300]}

    # Parse response: candidates[0].content.parts[0].text → JSON
    # Gemini sometimes wraps the JSON in ```json...``` even with responseMimeType set.
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    cleaned = text
    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # drop first line (```json or ```) and last line (```)
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
    try:
        out = json.loads(cleaned)
    except Exception:
        # Last-ditch: extract verdict + reason from raw text via simple keyword search
        low = text.lower()
        verdict = "unclear"
        if any(s in low for s in ["verdict\":\"yes", "verdict\": \"yes", "yes,", "shows the", "depicts the"]):
            verdict = "yes"
        elif any(s in low for s in ["verdict\":\"no", "verdict\": \"no", "does not show", "not the"]):
            verdict = "no"
        return {
            "ok": False,
            "verdict": verdict,
            "confidence": 0.5 if verdict != "unclear" else 0.0,
            "reason": "fallback parse — Gemini returned non-JSON",
            "raw_text": text[:300],
        }

    confidence = float(out.get("confidence") or 0.0)
    return {
        "ok": True,
        "verdict": _finalize_verdict(out.get("verdict"), confidence),
        "confidence": confidence,
        "reason": out.get("reason") or "",
        "model": VISION_MODEL,
    }


def _same_from_json(out: dict) -> bool:
    """Fail-closed: identity is 'preserved' ONLY on a clear yes. Anything else
    (no / unclear / missing / low confidence) → False, so the caller uses the
    identity-safe deterministic composite instead of a possibly-mutated AI image."""
    verdict = str(out.get("same") if "same" in out else out.get("verdict") or "").strip().lower()
    if verdict in ("true", "yes"):
        return float(out.get("confidence") or 1.0) >= 0.6
    return False


def same_subject(source_path: str | Path, styled_path: str | Path) -> dict:
    """Did the stylise PRESERVE identity? Compares the original photo (image 1) with
    the stylised output (image 2): same person(s), face, and team kit? gpt-image-2's
    ref-edit can hallucinate a different player — this catches it. Fail-closed:
    returns {ok, same: bool, reason} with same=False on any doubt or error."""
    sp, tp = Path(source_path), Path(styled_path)
    if not sp.exists() or not tp.exists():
        return {"ok": False, "same": False, "reason": "image missing"}
    _env.load()
    try:
        api_key = _env.require("GEMINI_API_KEY")
    except RuntimeError:
        return {"ok": False, "same": False, "reason": "GEMINI_API_KEY missing"}

    def _part(p: Path) -> dict:
        ext = p.suffix.lower().lstrip(".") or "jpg"
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
        return {"inline_data": {"mime_type": mime, "data": base64.b64encode(p.read_bytes()).decode()}}

    prompt = (
        "Image 1 is an original photo of real footballer(s). Image 2 is a stylised/edited version "
        "of it (colour grade + a text overlay added). Is the SAME person/people shown — same face(s), "
        "same identity, same team kit/colours? Answer \"no\" if image 2 shows a DIFFERENT player, a "
        "different face, or a different kit (the editor sometimes hallucinates a new person). Be strict.\n"
        'Reply ONLY with JSON: {"same":true|false,"confidence":0.0,"reason":"<short>"}'
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}, _part(sp), _part(tp)]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1, "maxOutputTokens": 500},
    }
    url = f"{VISION_URL}?key={urllib.parse.quote(api_key)}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                headers={"Content-Type": "application/json", "User-Agent": "mandem-fc-agent/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except Exception as e:
        return {"ok": False, "same": False, "reason": f"vision error: {str(e)[:160]}"}
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:-1]).strip()
    try:
        out = json.loads(text)
    except Exception:
        return {"ok": False, "same": False, "reason": "non-JSON vision reply"}
    return {"ok": True, "same": _same_from_json(out), "confidence": float(out.get("confidence") or 0.0),
            "reason": out.get("reason") or "", "model": VISION_MODEL}


# ---------- CLI ----------

def _cli(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="vision_check")
    p.add_argument("--image", required=True)
    p.add_argument("--expected", required=True, help="what the image should show")
    args = p.parse_args(argv)
    print(json.dumps(assess(args.image, args.expected), indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
