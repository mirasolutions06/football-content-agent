#!/usr/bin/env python3
# scripts/mandem/wikimedia.py
# Wikimedia Commons image search via the MediaWiki action API.
# Returns CC-licensed images of named players / stadiums / clubs with author attribution.
#
# All Commons content is freely licensed (CC-BY-SA, CC-BY, CC0, public domain).
# When using a Commons image, the caller MUST credit the author + license — see
# the WikimediaImage.attribution property.

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "mandem-fc-agent/0.1 (football-social-agent)"


@dataclass
class WikimediaImage:
    title: str          # File:Foo.jpg
    url: str            # full-resolution URL
    thumb_url: str      # ~800px wide
    width: int
    height: int
    author: str         # raw HTML or text
    license: str        # e.g. "CC BY-SA 4.0"
    page_url: str       # human-readable page on commons.wikimedia.org

    @property
    def attribution(self) -> str:
        """Short attribution string suitable for an IG caption sub-line."""
        clean_author = _strip_html(self.author).strip() or "Wikimedia Commons"
        lic = self.license or "CC"
        return f"Photo: {clean_author} ({lic}, via Wikimedia Commons)"


def _api_get(params: dict) -> dict:
    qs = urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    req = urllib.request.Request(
        f"{API_URL}?{qs}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def search_titles(query: str, max_results: int = 10) -> list[str]:
    """Search Commons for image-bearing pages. Returns File: titles."""
    data = _api_get({
        "action": "query",
        "list": "search",
        "srsearch": query + " filetype:bitmap",
        "srnamespace": 6,        # File namespace
        "srlimit": max_results,
    })
    hits = (data.get("query") or {}).get("search") or []
    return [h["title"] for h in hits]


def fetch_imageinfo(titles: list[str]) -> list[WikimediaImage]:
    """Fetch URL + author + license for a batch of File: titles."""
    if not titles:
        return []
    data = _api_get({
        "action": "query",
        "titles": "|".join(titles),
        "prop": "imageinfo",
        "iiprop": "url|size|extmetadata",
        "iiurlwidth": "1200",
    })
    pages = (data.get("query") or {}).get("pages") or []
    out: list[WikimediaImage] = []
    # Pages may be returned as a list (formatversion=2) or dict; handle both.
    if isinstance(pages, dict):
        pages = list(pages.values())
    for p in pages:
        title = p.get("title") or ""
        info = (p.get("imageinfo") or [{}])[0]
        ext = info.get("extmetadata") or {}
        author_raw = (ext.get("Artist") or {}).get("value") or ""
        license_short = (ext.get("LicenseShortName") or {}).get("value") or ""
        out.append(WikimediaImage(
            title=title,
            url=info.get("url") or "",
            thumb_url=info.get("thumburl") or info.get("url") or "",
            width=int(info.get("width") or 0),
            height=int(info.get("height") or 0),
            author=author_raw,
            license=license_short,
            page_url=info.get("descriptionurl") or "",
        ))
    return [w for w in out if w.url]


def search(query: str, max_results: int = 5) -> list[WikimediaImage]:
    return fetch_imageinfo(search_titles(query, max_results))


def download(img: WikimediaImage, out_dir: Path, filename_hint: str = "wiki") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    url = img.thumb_url or img.url
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": "https://commons.wikimedia.org/"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
        ctype = r.headers.get("Content-Type", "")
    ext = "jpg"
    if "png" in ctype:
        ext = "png"
    elif "webp" in ctype:
        ext = "webp"
    out = out_dir / f"{filename_hint}_{int(time.time())}.{ext}"
    out.write_bytes(data)
    return out


def search_and_download(query: str, out_dir: Path, filename_hint: str = "wiki") -> tuple[Path, WikimediaImage]:
    results = search(query, max_results=5)
    if not results:
        raise RuntimeError(f"no Wikimedia results for query={query!r}")
    top = results[0]
    path = download(top, out_dir=out_dir, filename_hint=filename_hint)
    return path, top


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _HTML_TAG_RE.sub("", s or "")


# ---------- CLI ----------

def _cli(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="wikimedia")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="search and print top results")
    s.add_argument("--query", required=True)
    s.add_argument("--max", type=int, default=5)
    d = sub.add_parser("download", help="search + download top result")
    d.add_argument("--query", required=True)
    from . import _env
    d.add_argument("--out-dir", default=str(_env.data_dir() / "images"))
    args = p.parse_args(argv)
    if args.cmd == "search":
        for i, r in enumerate(search(args.query, max_results=args.max), 1):
            print(f"  [{i}] {r.title}  {r.width}x{r.height}  ({r.license})")
            print(f"      author: {_strip_html(r.author)[:80]}")
            print(f"      url:    {r.url}")
    elif args.cmd == "download":
        path, meta = search_and_download(args.query, Path(args.out_dir))
        print(f"  saved: {path}")
        print(f"  license: {meta.license}")
        print(f"  author:  {_strip_html(meta.author)[:80]}")
        print(f"  attribution line: {meta.attribution}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
