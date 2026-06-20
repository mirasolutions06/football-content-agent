# scripts/mandem/models.py
# Football data models adapted from an earlier football bot.
# Adds Mandem-specific event types (none for MVP — set is identical).

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class EventType(Enum):
    MATCH_PREVIEW = "match_preview"
    GOAL = "goal"
    HALF_TIME = "half_time"
    FULL_TIME = "full_time"
    RED_CARD = "red_card"
    STANDINGS = "standings"


@dataclass
class Team:
    id: int
    name: str


@dataclass
class Fixture:
    id: int
    league_id: int
    league_name: str
    home_team: Team
    away_team: Team
    kickoff: str            # ISO 8601
    status: str             # NS, 1H, HT, 2H, FT, AET, PEN, etc.
    home_score: Optional[int]
    away_score: Optional[int]

    @classmethod
    def from_api(cls, data: dict) -> "Fixture":
        return cls(
            id=data["fixture"]["id"],
            league_id=data["league"]["id"],
            league_name=data["league"]["name"],
            home_team=Team(
                id=data["teams"]["home"]["id"],
                name=data["teams"]["home"]["name"],
            ),
            away_team=Team(
                id=data["teams"]["away"]["id"],
                name=data["teams"]["away"]["name"],
            ),
            kickoff=data["fixture"]["date"],
            status=data["fixture"]["status"]["short"],
            home_score=data["goals"]["home"],
            away_score=data["goals"]["away"],
        )

    @property
    def score_str(self) -> str:
        if self.home_score is None or self.away_score is None:
            return f"{self.home_team.name} vs {self.away_team.name}"
        return f"{self.home_team.name} {self.home_score}-{self.away_score} {self.away_team.name}"

    @property
    def is_finished(self) -> bool:
        return self.status in {"FT", "AET", "PEN"}
