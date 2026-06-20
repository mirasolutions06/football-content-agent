# scripts/mandem/importance.py
# Event importance scoring adapted from an earlier football bot.
# Tweak _BIG_TEAMS / _RIVALRIES here to bias what Mandem treats as a high-stakes event.

"""
Event importance scoring (1-10).

A 90th-minute equaliser in a title clash = 10. A 12th-minute tap-in in a
dead rubber = 2. The score drives how much energy the bot puts into its reaction.
"""

BIG_TEAMS = {
    "Arsenal", "Chelsea", "Liverpool", "Manchester City",
    "Manchester United", "Tottenham",
    "Real Madrid", "Barcelona", "Bayern Munich", "PSG",
    "Juventus", "AC Milan", "Inter",
}

RIVALRIES = [
    {"Arsenal", "Tottenham"},
    {"Arsenal", "Chelsea"},
    {"Liverpool", "Manchester United"},
    {"Liverpool", "Everton"},
    {"Manchester City", "Manchester United"},
    {"Chelsea", "Tottenham"},
    {"AC Milan", "Inter"},
    {"Real Madrid", "Barcelona"},
    {"Real Madrid", "Atletico Madrid"},
    {"Bayern Munich", "Borussia Dortmund"},
]

# Underscored aliases retained for any internal callers that still use them.
_BIG_TEAMS = BIG_TEAMS
_RIVALRIES = RIVALRIES


def calculate_importance(
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    minute: int,
    event_type: str,
    league_name: str = "",
) -> int:
    """Return 1-10 for a match event."""
    score = 3

    if "Champions League" in league_name or "UEFA" in league_name:
        score += 2
    elif "Premier League" in league_name:
        score += 1

    home_is_big = home_team in _BIG_TEAMS
    away_is_big = away_team in _BIG_TEAMS
    if home_is_big or away_is_big:
        score += 1
    if home_is_big and away_is_big:
        score += 1

    if {home_team, away_team} in _RIVALRIES:
        score += 2

    if minute >= 85:
        score += 2
    elif minute >= 75:
        score += 1

    if event_type == "goal":
        diff = abs(home_score - away_score)
        if home_score == away_score:
            score += 2
        elif diff == 1 and minute >= 70:
            score += 1
        if diff >= 3:
            score -= 1

    if event_type == "red_card" and minute <= 30:
        score += 1

    return max(1, min(10, score))


def importance_for_fulltime(
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    league_name: str = "",
    had_red_card: bool = False,
) -> int:
    """Importance score for a full-time event.

    No 'minute' for FT — we score on the final scoreline + competition + rivalry.
    """
    score = 3

    if "Champions League" in league_name or "UEFA" in league_name:
        score += 2
    elif "Premier League" in league_name:
        score += 1

    home_is_big = home_team in _BIG_TEAMS
    away_is_big = away_team in _BIG_TEAMS
    if home_is_big or away_is_big:
        score += 1
    if home_is_big and away_is_big:
        score += 1

    if {home_team, away_team} in _RIVALRIES:
        score += 2

    diff = abs(home_score - away_score)
    total = home_score + away_score

    if total >= 5:
        score += 2          # high-scoring thriller
    elif total >= 4:
        score += 1

    if diff == 0 and total >= 2:
        score += 1          # entertaining draw

    if diff >= 4:
        score += 1          # statement battering

    if had_red_card:
        score += 1

    return max(1, min(10, score))


def importance_for_preview(home_team: str, away_team: str, league_name: str = "") -> int:
    """Pre-kickoff importance score (1-10). No scoreline / minute yet —
    judges purely on competition + which teams are involved + rivalry."""
    score = 3
    if "Champions League" in league_name or "UEFA" in league_name:
        score += 2
    elif "Premier League" in league_name:
        score += 1
    home_is_big = home_team in BIG_TEAMS
    away_is_big = away_team in BIG_TEAMS
    if home_is_big or away_is_big:
        score += 1
    if home_is_big and away_is_big:
        score += 2
    if {home_team, away_team} in RIVALRIES:
        score += 2
    return max(1, min(10, score))
