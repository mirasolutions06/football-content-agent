#!/usr/bin/env python3
# scripts/tests/test_overlay_word.py
# The baked overlay word must match the caption the brain wrote. The skill puts the
# overlay slug on the caption's FIRST LINE (ALL-CAPS) — use that instead of re-rolling
# a fresh Gemini word (which gave MONARCH on a ROYALTY caption). Falls back to None
# (→ make_overlay_phrase) when the caption doesn't lead with a clean slug.
# Run: python3 scripts/tests/test_overlay_word.py

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mandem.stylize import overlay_from_caption  # noqa: E402


def test_uses_allcaps_first_line():
    assert overlay_from_caption("ROYALTY\n\nDeschamps running bodyguard duty.") == "ROYALTY"


def test_two_word_slug_kept():
    assert overlay_from_caption("HEAVY METAL\n\nMo said it with chest.") == "HEAVY METAL"


def test_allows_question_slug():
    assert overlay_from_caption("DROP HIM?\n\nForm's gone.") == "DROP HIM?"


def test_strips_trailing_punctuation():
    assert overlay_from_caption("ROYALTY.\n\nbody") == "ROYALTY"


def test_normal_sentence_first_line_falls_back():
    # the bug: a prose first line is NOT a slug → fall back to make_overlay_phrase
    assert overlay_from_caption("Messi did it again at 38, ridiculous little man.") is None


def test_too_many_words_falls_back():
    assert overlay_from_caption("TOP FOUR OR BUST\n\nbody") is None


def test_banned_word_falls_back():
    # never bake a character-assassination word even if the caption leads with one
    assert overlay_from_caption("FRAUD\n\nbody") is None


def test_empty_falls_back():
    assert overlay_from_caption("") is None
    assert overlay_from_caption("   \n\nbody") is None


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
