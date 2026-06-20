#!/usr/bin/env python3
# scripts/mandem/season_mode.py
# Single source of truth for "what football is live right now" — the content mode.
#
# The club season runs ~Aug–May. Over the summer and during international breaks the
# club leagues go dark and the live football is a tournament (World Cup / Euros) plus
# the transfer window. This module swaps the agent's covered competitions + news feeds
# + persona voice based on a small date-window calendar, and auto-reverts to club mode
# when the leagues return. Reusable for every future break: just add a Window.
#
# Runtime override: MANDEM_MODE = auto (default) | club | summer.
#
#   python3 -m scripts.mandem.season_mode      # print the currently-resolved mode
#
# Imports nothing from footy_api (footy_api re-exports CLUB_LEAGUES from here), so
# there is no import cycle.

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

# Club competitions (API-Football league IDs) — the baseline / default mode.
CLUB_LEAGUES: dict[int, str] = {
    39:  "Premier League",
    2:   "UEFA Champions League",
    140: "La Liga",
    135: "Serie A",
    78:  "Bundesliga",
    61:  "Ligue 1",
}

# Tournament competition sets (API-Football league IDs).
WORLD_CUP: dict[int, str] = {1: "FIFA World Cup"}


@dataclass(frozen=True)
class Window:
    """A date range where coverage swaps away from the club default."""
    name: str
    start: date
    end: date  # inclusive
    competitions: dict[int, str]
    transfers_on: bool
    voice: str

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


@dataclass(frozen=True)
class Mode:
    """The resolved content mode for a given day."""
    name: str
    competitions: dict[int, str]
    transfers_on: bool
    voice: str


# Persona voice notes — short on purpose. Light-touch: the brain carries the actual
# football knowledge; this just sets the situational frame.
_SUMMER_VOICE = (
    "SUMMER MODE: club leagues are off until mid-August. You're covering the World Cup "
    "(national teams — group stage then knockouts) and the open transfer window. Bring "
    "international-tournament energy, and transfer-window banter (HERE WE GO / done deal / "
    "medical booked / 'who's he?') when a story earns it. Football only; all the usual "
    "caption rules still apply (no hashtags, tight word count)."
)
_CLUB_VOICE = "CLUB MODE: normal club-season coverage across the covered leagues."

# The calendar. First matching window wins; outside every window → club default.
# Add a Window here for each future tournament / international break.
WINDOWS: list[Window] = [
    Window(
        name="summer-2026",
        start=date(2026, 6, 1),
        end=date(2026, 8, 14),
        competitions=WORLD_CUP,
        transfers_on=True,
        voice=_SUMMER_VOICE,
    ),
]


def _club_mode() -> Mode:
    return Mode(name="club", competitions=dict(CLUB_LEAGUES), transfers_on=False, voice=_CLUB_VOICE)


def _window_mode(w: Window) -> Mode:
    return Mode(name=w.name, competitions=dict(w.competitions), transfers_on=w.transfers_on, voice=w.voice)


def current_mode(today: date | None = None) -> Mode:
    """Resolve the active content mode for `today` (defaults to the real today).

    MANDEM_MODE env overrides the date logic:
      auto (default) — use the window calendar
      club           — force club mode
      summer         — force the first (seed) summer window
    """
    override = (os.environ.get("MANDEM_MODE") or "auto").strip().lower()
    if override == "club":
        return _club_mode()
    if override == "summer" and WINDOWS:
        return _window_mode(WINDOWS[0])

    d = today or date.today()
    for w in WINDOWS:
        if w.contains(d):
            return _window_mode(w)
    return _club_mode()


def active_competitions(today: date | None = None) -> dict[int, str]:
    """The competition set live right now (mode-aware). Prefer this over CLUB_LEAGUES."""
    return current_mode(today).competitions


if __name__ == "__main__":
    m = current_mode()
    print(f"mode={m.name}  transfers_on={m.transfers_on}")
    print("competitions:", m.competitions)
    print("voice:", m.voice)
