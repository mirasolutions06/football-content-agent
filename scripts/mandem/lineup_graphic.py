#!/usr/bin/env python3
# scripts/mandem/lineup_graphic.py
# Render a 1080x1080 lineup graphic for the Mandem agent's pre-match post.
# Pure Pillow — no AI gen on this path. Lineup data shape follows API-Football's
# /fixtures/lineups response.

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from . import _env

DEFAULT_OUT_DIR = _env.data_dir() / "images"

W, H = 1080, 1080
BG = (15, 18, 26)
DIVIDER = (80, 80, 100)
HEADER = (220, 220, 220)
TEAM_COLOR = (255, 255, 255)
NUM_COLOR = (255, 200, 80)
NAME_COLOR = (245, 245, 245)
WATERMARK = (100, 100, 130)


def _font(size: int) -> ImageFont.ImageFont:
    """Load a bold sans-serif. Fallback chain handles macOS/Linux/built-in."""
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",                                  # macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",                 # Ubuntu/Debian
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",         # Liberation
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",                             # Arch
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render(fixture_id: int, lineups: list[dict[str, Any]],
           out_dir: Path | None = None) -> Path:
    """Render and save a 1080x1080 lineup graphic. Returns the output path.

    `lineups` is API-Football's /fixtures/lineups response array.
    """
    out_dir = out_dir or DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"lineup_{fixture_id}.png"

    img = Image.new("RGB", (W, H), color=BG)
    d = ImageDraw.Draw(img)

    title_font = _font(56)
    team_font = _font(40)
    name_font = _font(28)
    num_font = _font(28)

    if len(lineups) < 2:
        d.text((W // 2, H // 2), "(lineups missing)", fill=(180, 60, 60),
               font=team_font, anchor="mm")
        img.save(out_path, "PNG", optimize=True)
        return out_path

    home, away = lineups[0], lineups[1]

    # Header
    d.text((W // 2, 60), "STARTING XI", fill=HEADER, font=title_font, anchor="mm")
    d.text((W // 4, 140), home.get("team", {}).get("name", "Home"),
           fill=TEAM_COLOR, font=team_font, anchor="mm")
    d.text((3 * W // 4, 140), away.get("team", {}).get("name", "Away"),
           fill=TEAM_COLOR, font=team_font, anchor="mm")

    # Center divider
    d.line([(W // 2, 200), (W // 2, H - 80)], fill=DIVIDER, width=2)

    # Two columns of starters
    for col, side in [(0, home), (1, away)]:
        x_num = W // 4 - 180 if col == 0 else 3 * W // 4 - 180
        x_name = W // 4 - 130 if col == 0 else 3 * W // 4 - 130
        starters = (side.get("startXI") or [])[:11]
        for i, sp in enumerate(starters):
            p = sp.get("player") or {}
            num = str(p.get("number") or "")
            name = (p.get("name") or "?")[:24]  # truncate long names
            y = 230 + i * 55
            d.text((x_num, y), num, fill=NUM_COLOR, font=num_font, anchor="lt")
            d.text((x_name, y), name, fill=NAME_COLOR, font=name_font, anchor="lt")

    # Watermark
    d.text((W // 2, H - 40), "MANDEM FC", fill=WATERMARK, font=num_font, anchor="mm")

    img.save(out_path, "PNG", optimize=True)
    return out_path


# ---------- CLI ----------

def _cli(argv: list[str]) -> int:
    """python -m scripts.mandem.lineup_graphic test  → render a synthetic Arsenal-Spurs graphic."""
    import argparse
    p = argparse.ArgumentParser(prog="lineup_graphic")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("test", help="render a synthetic test graphic")
    args = p.parse_args(argv)
    if args.cmd == "test":
        synth = [
            {"team": {"name": "Arsenal"}, "startXI": [
                {"player": {"name": n, "number": num}}
                for num, n in [(1, "Raya"), (2, "Saliba"), (3, "Gabriel"), (4, "White"),
                               (5, "Lewis-Skelly"), (8, "Odegaard"), (11, "Rice"),
                               (7, "Saka"), (10, "Eze"), (29, "Trossard"), (14, "Havertz")]]},
            {"team": {"name": "Tottenham"}, "startXI": [
                {"player": {"name": n, "number": num}}
                for num, n in [(1, "Vicario"), (33, "Davies"), (6, "Romero"), (37, "van de Ven"),
                               (23, "Porro"), (8, "Bissouma"), (10, "Maddison"),
                               (20, "Kulusevski"), (7, "Son"), (11, "Werner"), (9, "Solanke")]]},
        ]
        path = render(fixture_id=999, lineups=synth)
        print(f"  saved: {path}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
