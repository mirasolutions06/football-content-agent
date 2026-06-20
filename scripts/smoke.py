#!/usr/bin/env python3
# scripts/smoke.py
# Pre-deploy smoke tests for the Mandem FC pipeline. Catches regressions in:
#   - Provider keys (env loaded, all required vars present)
#   - RSS feed schemas (BBC, Guardian, ESPN, Sky)
#   - API-Football contract (only with --live; costs 1/100 daily req)
#
# Usage:
#   python3 scripts/smoke.py           # cheap checks (no live API calls beyond RSS)
#   python3 scripts/smoke.py --live    # also hits API-Football /status
#
# Exits 0 if everything passes, 1 if any check fails. Prints a one-line
# verdict per check.

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from xml.etree import ElementTree as ET

# Make `from mandem...` work whether invoked from repo root or scripts/
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from mandem import _env  # noqa: E402
from mandem.news_rss import RSS_FEEDS, USER_AGENT  # noqa: E402

REQUIRED_KEYS = [
    "FAL_KEY",              # Seedream v4 stylise engine + aura-sr upscale (REQUIRED for stylise)
    "GEMINI_API_KEY",       # overlay phrase (gemini-2.5-flash) + same_subject identity QC
    "OPENAI_API_KEY",       # gpt-image-2 text-to-image for generate_brand_image (brand fallback)
    "MANDEM_BOT_TOKEN",
    "MJ_MANDEM_CHAT_ID",
]
# At least one of these for API-Football auth
APIFOOTBALL_KEYS = ["APISPORTS_KEY", "RAPIDAPI_KEY"]
# Optional fallbacks — warn but don't fail
OPTIONAL_KEYS = ["BRAVE_API_KEY", "PEXELS_API_KEY"]


def _ok(label: str, detail: str = "") -> None:
    print(f"  ok    {label}" + (f"  ({detail})" if detail else ""))


def _fail(label: str, detail: str) -> None:
    print(f"  FAIL  {label}  — {detail}")


def _warn(label: str, detail: str) -> None:
    print(f"  warn  {label}  — {detail}")


def check_keys() -> int:
    print("[1/3] Provider keys")
    _env.load()
    failed = 0

    for k in REQUIRED_KEYS:
        if os.environ.get(k):
            _ok(k)
        else:
            _fail(k, "missing - set in .env or a server environment file")
            failed += 1

    if any(os.environ.get(k) for k in APIFOOTBALL_KEYS):
        present = [k for k in APIFOOTBALL_KEYS if os.environ.get(k)]
        _ok("APIFOOTBALL", f"using {present[0]}")
    else:
        _fail("APIFOOTBALL", "neither APISPORTS_KEY nor RAPIDAPI_KEY set")
        failed += 1

    for k in OPTIONAL_KEYS:
        if os.environ.get(k):
            _ok(k)
        else:
            _warn(k, "optional — search ladder will skip this engine")
    return failed


def check_rss_feeds() -> int:
    print("[2/3] RSS feeds parse")
    failed = 0
    for src, meta in RSS_FEEDS.items():
        url = meta["url"]
        name = meta["name"]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            _fail(name, f"fetch failed: {e}")
            failed += 1
            continue
        try:
            tree = ET.fromstring(body)
        except ET.ParseError as e:
            _fail(name, f"xml parse failed: {e}")
            failed += 1
            continue
        # Both RSS 2.0 (<rss><channel><item>) and Atom (<feed><entry>) should
        # yield at least one item — bail loud if neither matches.
        items = tree.findall(".//item") or tree.findall(".//{http://www.w3.org/2005/Atom}entry")
        if not items:
            _fail(name, "no <item> or <entry> elements")
            failed += 1
        else:
            _ok(name, f"{len(items)} items")
    return failed


def check_apifootball_live() -> int:
    print("[3/3] API-Football /status (live, costs 1 req)")
    key = os.environ.get("APISPORTS_KEY") or os.environ.get("RAPIDAPI_KEY")
    if not key:
        _fail("apifootball", "no key in env (already flagged in check 1)")
        return 1
    headers = {"x-apisports-key": key} if os.environ.get("APISPORTS_KEY") else {
        "x-rapidapi-key": key,
        "x-rapidapi-host": "v3.football.api-sports.io",
    }
    req = urllib.request.Request(
        "https://v3.football.api-sports.io/status", headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            import json
            data = json.loads(r.read())
    except Exception as e:
        _fail("apifootball /status", str(e))
        return 1
    resp = (data or {}).get("response") or {}
    requests_used = resp.get("requests", {}).get("current")
    requests_limit = resp.get("requests", {}).get("limit_day")
    if requests_used is None:
        _fail("apifootball /status", f"unexpected shape: keys={list(resp.keys())[:5]}")
        return 1
    _ok("apifootball /status", f"{requests_used}/{requests_limit} req used today")
    return 0


def main(argv: list[str]) -> int:
    live = "--live" in argv
    failed = 0
    failed += check_keys()
    failed += check_rss_feeds()
    if live:
        failed += check_apifootball_live()
    else:
        print("[3/3] skipping --live API-Football check (pass --live to enable)")
    print()
    if failed:
        print(f"FAILED — {failed} check(s) failed")
        return 1
    print("PASSED — all checks green")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
