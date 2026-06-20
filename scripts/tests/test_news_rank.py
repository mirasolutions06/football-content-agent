#!/usr/bin/env python3
# scripts/tests/test_news_rank.py
# Image-search candidates are ranked so hi-res surfaces first and tiny thumbnails
# (which stylise to mush) are dropped. Run: python3 scripts/tests/test_news_rank.py

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mandem.news_image import _rank_candidates  # noqa: E402


def test_drops_known_tiny():
    out = _rank_candidates([
        {"url": "tiny", "width": 300, "height": 200},
        {"url": "big", "width": 1920, "height": 1080},
    ])
    assert [d["url"] for d in out] == ["big"]


def test_sorts_largest_first():
    out = _rank_candidates([
        {"url": "small", "width": 800, "height": 800},
        {"url": "large", "width": 2000, "height": 2000},
    ])
    assert [d["url"] for d in out] == ["large", "small"]


def test_unknown_dims_kept_ranked_last():
    out = _rank_candidates([
        {"url": "unknown", "width": 0, "height": 0},
        {"url": "big", "width": 1500, "height": 1500},
    ])
    assert [d["url"] for d in out] == ["big", "unknown"]


def test_error_items_survive():
    items = [{"engine": "brave", "error": "boom"}]
    assert _rank_candidates(items) == items


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
