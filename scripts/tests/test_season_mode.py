#!/usr/bin/env python3
# scripts/tests/test_season_mode.py
# Plain-python tests for the content-mode date logic (repo has no pytest config).
# Run:  python3 scripts/tests/test_season_mode.py

import os
import sys
from datetime import date
from pathlib import Path

# Put scripts/ on the path so `mandem` imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mandem.season_mode import (  # noqa: E402
    CLUB_LEAGUES,
    WORLD_CUP,
    active_competitions,
    current_mode,
)


def _clear_override():
    os.environ.pop("MANDEM_MODE", None)


def test_inside_summer_window():
    _clear_override()
    m = current_mode(date(2026, 7, 1))
    assert m.name == "summer-2026", m.name
    assert m.competitions == WORLD_CUP, m.competitions
    assert m.transfers_on is True


def test_outside_window_is_club():
    _clear_override()
    m = current_mode(date(2026, 10, 1))
    assert m.name == "club", m.name
    assert m.competitions == CLUB_LEAGUES
    assert m.transfers_on is False


def test_window_boundaries_inclusive():
    _clear_override()
    assert current_mode(date(2026, 6, 1)).name == "summer-2026"   # start inclusive
    assert current_mode(date(2026, 8, 14)).name == "summer-2026"  # end inclusive
    assert current_mode(date(2026, 8, 15)).name == "club"         # day after → club


def test_env_override_club_forces_club_inside_window():
    os.environ["MANDEM_MODE"] = "club"
    try:
        m = current_mode(date(2026, 7, 1))  # inside window, but forced club
        assert m.name == "club"
        assert m.competitions == CLUB_LEAGUES
    finally:
        _clear_override()


def test_env_override_summer_forces_summer_outside_window():
    os.environ["MANDEM_MODE"] = "summer"
    try:
        m = current_mode(date(2026, 10, 1))  # outside window, but forced summer
        assert m.name == "summer-2026"
        assert m.competitions == WORLD_CUP
    finally:
        _clear_override()


def test_active_competitions_matches_mode():
    _clear_override()
    assert active_competitions(date(2026, 7, 1)) == WORLD_CUP
    assert active_competitions(date(2026, 10, 1)) == CLUB_LEAGUES


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
