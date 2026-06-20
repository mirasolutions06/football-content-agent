#!/usr/bin/env python3
# scripts/tests/test_vision_verdict.py
# Fail-closed verdict logic for the image relevance check: a hedged/low-confidence
# "yes" must NOT pass (it would waste a paid stylize on a wrong image).
# Run: python3 scripts/tests/test_vision_verdict.py

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mandem.vision_check import _finalize_verdict, _same_from_json  # noqa: E402


def test_confident_yes_passes():
    assert _finalize_verdict("yes", 0.92) == "yes"


def test_low_confidence_yes_is_downgraded():
    # the exact bug: a hedged "yes" let a wrong image through to stylize
    assert _finalize_verdict("yes", 0.40) == "unclear"


def test_yes_at_threshold_passes():
    assert _finalize_verdict("yes", 0.55) == "yes"


def test_no_stays_no():
    assert _finalize_verdict("no", 0.95) == "no"


def test_unknown_verdict_becomes_unclear():
    assert _finalize_verdict("maybe", 0.9) == "unclear"


def test_case_insensitive():
    assert _finalize_verdict("YES", 0.9) == "yes"


# --- same_subject identity QC (fail-closed): only a confident yes means "preserved" ---

def test_same_true_high_conf():
    assert _same_from_json({"same": True, "confidence": 0.9}) is True


def test_same_false_is_false():
    assert _same_from_json({"same": False, "confidence": 0.9}) is False


def test_same_low_conf_fails_closed():
    assert _same_from_json({"same": True, "confidence": 0.3}) is False


def test_same_missing_fails_closed():
    assert _same_from_json({}) is False


def test_same_accepts_verdict_yes_alias():
    assert _same_from_json({"verdict": "yes", "confidence": 0.8}) is True


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
