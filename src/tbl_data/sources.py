"""Every remote document the pipeline knows about, as Resource builders."""

from __future__ import annotations

from urllib.parse import urlencode

from .config import GENIUS_DATA_URL, TBF_ORIGIN, Season
from .fetch import Resource

API = f"{TBF_ORIGIN}/api"


def _tbf(kind: str, rid: str, path: str, params: dict | None = None, method: str = "GET") -> Resource:
    url = f"{API}/{path}" + (f"?{urlencode(params)}" if params else "")
    return Resource("tbf", kind, rid, url, method)


# -- season level ------------------------------------------------------------------
def league_weeks(s: Season) -> Resource:
    return _tbf("league_weeks", str(s.league_id), "League/get-league-weeks", {"leagueId": s.league_id, "seasonId": s.season_id})


def league_info(s: Season) -> Resource:
    return _tbf("league_info", str(s.league_id), "League/get-league-info", {"leagueId": s.league_id})


def teams(s: Season) -> Resource:
    # (sic) "leauge" is the API's spelling
    return _tbf("teams", str(s.league_id), "Team/get-teams-by-leauge", {"leaugeId": s.league_id})


def player_list(s: Season) -> Resource:
    return _tbf(
        "player_list", f"{s.season_id}_{s.league_id}", "Player/get-player-list",
        {"seasonId": s.season_id, "leagueId": s.league_id, "page": 1, "pageSize": -1},
    )


def fixture_week(s: Season, week: str, half_value: str | None) -> Resource:
    params = {"ActivityId": s.league_id, "Page": 1, "PageSize": -1, "WeekFilter": week}
    if half_value:
        params["HalfValue"] = half_value
    return _tbf("fixture_week", f"{s.league_id}_w{int(week):02d}", "Match/get-all-matches-for-filter", params)


def season_resources(s: Season) -> list[Resource]:
    return [league_weeks(s), league_info(s), teams(s), player_list(s)]


# -- match level -------------------------------------------------------------------
TBF_MATCH_ENDPOINTS = {
    "match_header": "Match/mac-header",
    "match_summary": "Match/mac-ozet",
    "match_boxscore": "Match/mac-istatistik",
    "match_gameflow": "Match/game-flow",
    "match_shotchart": "Match/shot-chart",
    "match_leaders": "Match/mac-leader",
    "match_report": "Match/get-match-report-with-players",
}


def tbf_match(match_id: int) -> list[Resource]:
    return [_tbf(kind, str(match_id), path, {"matchId": match_id}) for kind, path in TBF_MATCH_ENDPOINTS.items()]


def genius_match(genius_id: str) -> Resource:
    return Resource("genius", "livestats", str(genius_id), GENIUS_DATA_URL.format(genius_id=genius_id))


# -- player level ------------------------------------------------------------------
def player(s: Season, player_id: int) -> list[Resource]:
    pid = int(player_id)
    return [
        _tbf("player_profile", str(pid), f"Player/get-player-detail-profile-and-career-info/{pid}"),
        _tbf("player_career_seasons", str(pid), f"Player/{pid}/career-seasons"),
        _tbf("player_season_games", f"{s.season_id}_{pid}",
             f"Player/get-player-detail-match-statistic/{s.season_id}/{pid}", method="POST"),
    ]


PLAYER_KINDS = ("player_profile", "player_career_seasons", "player_season_games")
