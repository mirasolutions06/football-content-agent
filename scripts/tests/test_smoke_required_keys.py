#!/usr/bin/env python3
# scripts/tests/test_smoke_required_keys.py
# The pre-deploy smoke test must gate on FAL_KEY — the Seedream stylise engine cannot
# run without it, so a green smoke that ignores it would wave through a broken deploy.
# Run: python3 scripts/tests/test_smoke_required_keys.py

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import smoke  # noqa: E402


def test_fal_key_is_required():
    assert "FAL_KEY" in smoke.REQUIRED_KEYS, smoke.REQUIRED_KEYS


def test_core_keys_still_required():
    for k in ("GEMINI_API_KEY", "MANDEM_BOT_TOKEN", "MJ_MANDEM_CHAT_ID"):
        assert k in smoke.REQUIRED_KEYS, k


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
