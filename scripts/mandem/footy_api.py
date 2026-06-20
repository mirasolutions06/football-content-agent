#!/usr/bin/env python3
# scripts/mandem/footy_api.py
# API-Football v3 client adapted from an earlier football bot.
# Wraps API-Football v3 (RapidAPI). Free tier = 100 req/day.
#
# Usage:
#   python3 -m scripts.mandem.footy_api live                  # all live fixtures
#   python3 -m scripts.mandem.footy_api covered               # live fixtures in covered leagues only
#   python3 -m scripts.mandem.footy_api upcoming <league_id>  # next fixtures for a league
#   python3 -m scripts.mandem.footy_api events <fixture_id>   # raw event list for a fixture
#
# Reads RAPIDAPI_KEY from environment.

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import List

import httpx

from . import _env
from .models import Fixture
from .season_mode import CLUB_LEAGUES, active_competitions

BASE_URL = "https://v3.football.api-sports.io"

# The covered competition set is mode-aware (club season vs a summer tournament
# window) — see season_mode.py. CLUB_LEAGUES is the club-season baseline; runtime
# code should prefer active_competitions(). COVERED_LEAGUES stays as a back-compat
# alias for the baseline so existing imports keep working.
COVERED_LEAGUES: dict[int, str] = CLUB_LEAGUES


class FootballClient:
    """API-Football v3 wrapper.

    Supports both auth styles against the same v3.football.api-sports.io endpoint:
      - APISPORTS_KEY: signed up direct at dashboard.api-football.com (simpler)
      - RAPIDAPI_KEY:  signed up via RapidAPI marketplace
    Whichever is present in env wins; APISPORTS_KEY takes priority if both set.
    """
    def __init__(self, api_key: str | None = None):
        _env.load()
        if api_key:
            self._key = api_key
            self._headers = {"x-apisports-key": api_key}
            return
        apisports = os.environ.get("APISPORTS_KEY")
        rapidapi = os.environ.get("RAPIDAPI_KEY")
        if apisports:
            self._key = apisports
            self._headers = {"x-apisports-key": apisports}
        elif rapidapi:
            self._key = rapidapi
            self._headers = {
                "x-rapidapi-key": rapidapi,
                "x-rapidapi-host": "v3.football.api-sports.io",
            }
        else:
            raise RuntimeError(
                "No API-Football auth set. Add APISPORTS_KEY (from dashboard.api-football.com) "
                "or RAPIDAPI_KEY (from rapidapi.com) to .env or the server environment file."
            )

    async def get_live_fixtures(self) -> List[Fixture]:
        """One call returns all currently live fixtures across all leagues."""
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{BASE_URL}/fixtures",
                headers=self._headers,
                params={"live": "all"},
            )
            resp.raise_for_status()
            return [Fixture.from_api(f) for f in resp.json()["response"]]

    async def get_upcoming_fixtures(self, league_id: int, next_n: int = 10) -> List[Fixture]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{BASE_URL}/fixtures",
                headers=self._headers,
                params={"league": league_id, "next": next_n},
            )
            resp.raise_for_status()
            return [Fixture.from_api(f) for f in resp.json()["response"]]

    async def get_fixture_events(self, fixture_id: int) -> List[dict]:
        """Raw events (goals, cards, subs) for a specific fixture."""
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{BASE_URL}/fixtures/events",
                headers=self._headers,
                params={"fixture": fixture_id},
            )
            resp.raise_for_status()
            return resp.json()["response"]

    async def get_fixture(self, fixture_id: int) -> Fixture | None:
        """Fetch a single fixture by ID — used post-FT to confirm final state."""
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{BASE_URL}/fixtures",
                headers=self._headers,
                params={"id": fixture_id},
            )
            resp.raise_for_status()
            data = resp.json()["response"]
            if not data:
                return None
            return Fixture.from_api(data[0])

    async def get_lineups(self, fixture_id: int) -> List[dict]:
        """Lineups for a fixture. Available ~30-60min before kickoff. Future-phase use."""
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{BASE_URL}/fixtures/lineups",
                headers=self._headers,
                params={"fixture": fixture_id},
            )
            resp.raise_for_status()
            return resp.json()["response"]


def _filter_covered(fixtures: List[Fixture]) -> List[Fixture]:
    comps = active_competitions()
    return [f for f in fixtures if f.league_id in comps]


def _print_fixture(f: Fixture) -> None:
    print(f"  [{f.status}] {f.league_name}: {f.score_str}  (fixture_id={f.id})")


async def _cli_main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1

    cmd = argv[0]
    client = FootballClient()

    if cmd == "live":
        fixtures = await client.get_live_fixtures()
        if not fixtures:
            print("  (no live fixtures right now)")
            return 0
        print(f"  {len(fixtures)} live fixtures across all leagues:")
        for f in fixtures:
            _print_fixture(f)
        return 0

    if cmd == "covered":
        fixtures = await client.get_live_fixtures()
        covered = _filter_covered(fixtures)
        if not covered:
            print(f"  (no live fixtures in covered leagues right now; {len(fixtures)} live elsewhere)")
            return 0
        print(f"  {len(covered)} live in covered leagues:")
        for f in covered:
            _print_fixture(f)
        return 0

    if cmd == "upcoming":
        if len(argv) < 2:
            print("usage: upcoming <league_id>  (e.g. 39 = Premier League)")
            return 1
        league_id = int(argv[1])
        fixtures = await client.get_upcoming_fixtures(league_id, next_n=10)
        if not fixtures:
            print(f"  (no upcoming fixtures for league {league_id})")
            return 0
        for f in fixtures:
            print(f"  {f.kickoff}  {f.league_name}: {f.home_team.name} vs {f.away_team.name}  (id={f.id})")
        return 0

    if cmd == "events":
        if len(argv) < 2:
            print("usage: events <fixture_id>")
            return 1
        events = await client.get_fixture_events(int(argv[1]))
        print(json.dumps(events, indent=2, default=str))
        return 0

    print(f"unknown command: {cmd}")
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli_main(sys.argv[1:])))
