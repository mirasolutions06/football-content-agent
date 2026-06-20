#!/usr/bin/env python3
# scripts/tests/test_falimg.py
# The Seedream prompt builder injects the overlay word + headline colour and pins the
# locked style (flat bold, no shadow, shadowed real bg, top headline). No network.
# Run: python3 scripts/tests/test_falimg.py

import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mandem.falimg as fa  # noqa: E402
from mandem.falimg import seedream_prompt  # noqa: E402


class _Resp:
    """Minimal urlopen() context-manager stand-in."""
    def __init__(self, data):
        self._d = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._d


def _patch_call(fake_urlopen):
    """Swap falimg's urlopen + _key + time.sleep; return a restore() callable."""
    orig = (fa.urllib.request.urlopen, fa._key, fa.time.sleep)
    fa.urllib.request.urlopen = fake_urlopen
    fa._key = lambda: "test-key"
    fa.time.sleep = lambda *a, **k: None
    def restore():
        fa.urllib.request.urlopen, fa._key, fa.time.sleep = orig
    return restore


def test_includes_the_word():
    assert '"TIMELESS"' in seedream_prompt("TIMELESS")


def test_default_colour_is_orange():
    assert "orange" in seedream_prompt("SCENES").lower()


def test_colour_is_injectable():
    assert "gold" in seedream_prompt("LEGEND", color="gold").lower()


def test_pins_flat_no_shadow_style():
    p = seedream_prompt("BOTTLED")
    assert "NO drop shadow" in p and "FLAT" in p and "CONDENSED" in p


def test_keeps_player_and_real_background():
    p = seedream_prompt("CHAOS")
    assert "do NOT change or replace" in p and "NOT flat black" in p and "TOP third" in p


def test_call_retries_transient_5xx_then_succeeds():
    """A fal blip (429/5xx) must be retried, not swallowed into the plain composite."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError("https://fal.run/x", 503, "busy", {}, None)
        return _Resp(json.dumps({"ok": True}).encode())

    restore = _patch_call(fake_urlopen)
    try:
        out = fa._call("model", {"x": 1}, timeout=1)
        assert out == {"ok": True}, out
        assert calls["n"] == 3, f"expected 3 attempts, got {calls['n']}"
    finally:
        restore()


def test_call_does_not_retry_client_4xx():
    """A 4xx is the caller's fault — fail fast, don't burn retries (or fal credits)."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        raise urllib.error.HTTPError("https://fal.run/x", 400, "bad request", {}, None)

    restore = _patch_call(fake_urlopen)
    try:
        raised = False
        try:
            fa._call("model", {"x": 1})
        except urllib.error.HTTPError:
            raised = True
        assert raised, "400 should propagate"
        assert calls["n"] == 1, f"400 must NOT retry, got {calls['n']} attempts"
    finally:
        restore()


def test_call_gives_up_after_max_retries():
    """Persistent 5xx eventually raises (→ caught upstream → composite), bounded by _CALL_RETRIES."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        raise urllib.error.HTTPError("https://fal.run/x", 503, "busy", {}, None)

    restore = _patch_call(fake_urlopen)
    try:
        raised = False
        try:
            fa._call("model", {"x": 1})
        except urllib.error.HTTPError:
            raised = True
        assert raised, "persistent 503 should eventually raise"
        assert calls["n"] == fa._CALL_RETRIES, f"expected {fa._CALL_RETRIES} attempts, got {calls['n']}"
    finally:
        restore()


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
