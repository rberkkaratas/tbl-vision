"""Staged table contracts (pandera). The polars dtypes used to build each table come from here.

Keys: match_id = TBF matchId, player_id = TBF person id (kisiID, persistent across
seasons/leagues), team_season_id = TBF teamProcessId (one club in one season),
club_id = TBF teamId (persistent club).
"""

from __future__ import annotations

import pandera.polars as pa
import polars as pl
from pandera.engines.polars_engine import DateTime

Int = pl.Int32
# Field factories: a pandera Field instance must not be shared between columns.
def Count():
    return pa.Field(ge=0)


def OptCount():
    return pa.Field(ge=0, nullable=True)


def Opt():
    return pa.Field(nullable=True)

# Box score columns shared by Genius and TBF player rows (see parse/*: BOX_* maps).
BOX_STATS = (
    "seconds", "pts", "fgm", "fga", "fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta",
    "oreb", "dreb", "reb", "ast", "tov", "stl", "blk", "blk_against", "pf", "fouls_drawn",
    "plus_minus", "eff",
)


class _Box(pa.DataFrameModel):
    seconds: Int = Count()
    pts: Int = Count()
    fgm: Int = Count()
    fga: Int = Count()
    fg2m: Int = Count()
    fg2a: Int = Count()
    fg3m: Int = Count()
    fg3a: Int = Count()
    ftm: Int = Count()
    fta: Int = Count()
    oreb: Int = Count()
    dreb: Int = Count()
    reb: Int = Count()
    ast: Int = Count()
    tov: Int = Count()
    stl: Int = Count()
    blk: Int = Count()
    blk_against: Int = Count()
    pf: Int = Count()
    fouls_drawn: Int = Count()
    plus_minus: Int = Opt()
    eff: Int


class Matches(pa.DataFrameModel):
    match_id: Int
    genius_id: Int = Opt()
    week: Int
    phase: str  # "regular" or TBF playoff label
    start_utc: DateTime = pa.Field(dtype_kwargs={"time_zone": "UTC", "time_unit": "us"})
    venue: str = Opt()
    city: str = Opt()
    attendance: Int = OptCount()
    home_team_id: Int
    away_team_id: Int
    home_score: Int = Count()
    away_score: Int = Count()
    n_overtimes: Int = Count()
    period_minutes: Int = Opt()
    overtime_minutes: Int = Opt()
    referee_1: str = Opt()
    referee_2: str = Opt()
    referee_3: str = Opt()
    commissioner: str = Opt()
    youtube_url: str = Opt()
    broadcast: str = Opt()
    match_code: str = Opt()

    class Config:
        strict = True
        unique = ["match_id"]


class Teams(pa.DataFrameModel):
    team_season_id: Int
    club_id: Int
    name: str
    code: str = Opt()
    city: str = Opt()
    logo_url: str = Opt()
    color_1: str = Opt()
    color_2: str = Opt()

    class Config:
        strict = True
        unique = ["team_season_id"]


class Players(pa.DataFrameModel):
    player_id: Int
    full_name: str
    birth_date: pl.Date = Opt()
    nationality: str = Opt()
    nationality_code: str = Opt()
    height_cm: Int = Opt()
    weight_kg: Int = Opt()
    team_season_id: Int = Opt()  # current team per TBF player list
    jersey: str = Opt()
    photo_url: str = Opt()

    class Config:
        strict = True
        unique = ["player_id"]


class MatchTeams(_Box):
    match_id: Int
    team_season_id: Int
    is_home: pl.Boolean
    coach: str = Opt()
    assistant_coach: str = Opt()
    team_reb_off: Int = Count()
    team_reb_def: Int = Count()
    team_tov: Int = Count()
    team_fouls: Int = Count()
    pts_off_tov: Int = Count()
    pts_second_chance: Int = Count()
    pts_fast_break: Int = Count()
    pts_paint: Int = Count()
    pts_bench: Int = Count()
    biggest_lead: Int = Count()
    biggest_run: Int = Count()
    lead_changes: Int = Count()
    times_tied: Int = Count()
    time_leading_s: pl.Float64 = Count()

    class Config:
        strict = True
        unique = ["match_id", "team_season_id"]


class PeriodScores(pa.DataFrameModel):
    match_id: Int
    team_season_id: Int
    period_ord: Int = pa.Field(ge=1)
    period_label: str  # Q1..Q4, OT1..
    pts: Int = Count()

    class Config:
        strict = True
        unique = ["match_id", "team_season_id", "period_ord"]


class MatchPlayers(_Box):
    match_id: Int
    team_season_id: Int
    player_id: Int
    pno: Int  # Genius per-match player number (joins events/shots)
    jersey: str
    name: str
    starter: pl.Boolean
    played: pl.Boolean
    captain: pl.Boolean
    pts_second_chance: Int = Count()
    pts_fast_break: Int = Count()
    pts_paint: Int = Count()

    class Config:
        strict = True
        unique = ["match_id", "player_id"]


class Events(pa.DataFrameModel):
    match_id: Int
    action_number: Int
    period_ord: Int = pa.Field(ge=1)
    period_label: str
    clock: str  # raw MM:SS:cc, time remaining in period
    clock_s: pl.Float64 = Count()
    elapsed_s: pl.Float64 = Count()
    team_season_id: Int = Opt()
    is_home: pl.Boolean = Opt()
    pno: Int = Opt()
    player_id: Int = Opt()
    action_type: str
    sub_type: str = Opt()
    qualifiers: pl.List(pl.String)
    success: pl.Boolean
    scoring: pl.Boolean
    score_home: Int = Count()
    score_away: Int = Count()
    previous_action: Int = Opt()

    class Config:
        strict = True
        unique = ["match_id", "action_number"]


class Shots(pa.DataFrameModel):
    match_id: Int
    action_number: Int
    team_season_id: Int
    is_home: pl.Boolean
    player_id: Int
    pno: Int
    period_ord: Int = pa.Field(ge=1)
    x: pl.Float64 = pa.Field(ge=0, le=100)  # Genius court units, 0-100 over 28 m
    y: pl.Float64 = pa.Field(ge=0, le=100)  # 0-100 over 15 m
    made: pl.Boolean
    shot_type: str = pa.Field(isin=["2pt", "3pt"])
    sub_type: str = Opt()
    previous_action: Int = Opt()

    class Config:
        strict = True
        unique = ["match_id", "action_number"]


class PlayerCareer(pa.DataFrameModel):
    player_id: Int
    season: str
    team_name: str = Opt()
    competition: str = Opt()
    competition_area: str = Opt()
    registered_at: str = Opt()
    games: Int = Count()
    seconds: Int = Count()
    pts: Int = Count()
    fg2m: Int = Count()  # TBF calls this fieldGoalsMade, but it is 2P made
    fg2a: Int = Count()
    fg3m: Int = Count()
    fg3a: Int = Count()
    ftm: Int = Count()
    fta: Int = Count()
    oreb: Int = Count()
    dreb: Int = Count()
    reb: Int = Count()
    ast: Int = Count()
    stl: Int = Count()
    tov: Int = Count()
    blk: Int = Count()
    blk_against: Int = Count()
    pf: Int = Count()
    fouls_drawn: Int = Count()
    eff: Int

    class Config:
        strict = True


# -- TBF copies kept only to cross-check Genius ---------------------------------------
class TbfBox(_Box):
    match_id: Int
    team_season_id: Int
    player_id: Int
    jersey: str
    name: str
    starter: pl.Boolean

    class Config:
        strict = True
        unique = ["match_id", "player_id"]


class TbfEvents(pa.DataFrameModel):
    match_id: Int
    action_number: Int
    period: Int
    period_type: Int  # 1 regular, 2 overtime
    clock: str
    score: str = Opt()
    text: str  # Turkish description
    is_home: pl.Boolean = Opt()
    player_id: Int = Opt()

    class Config:
        strict = True


class TbfShots(pa.DataFrameModel):
    match_id: Int
    period: Int
    period_type: Int
    x: Int
    y: Int
    made: pl.Boolean
    is_home: pl.Boolean
    player_id: Int

    class Config:
        strict = True


TABLES: dict[str, type[pa.DataFrameModel]] = {
    "matches": Matches,
    "teams": Teams,
    "players": Players,
    "match_teams": MatchTeams,
    "period_scores": PeriodScores,
    "match_players": MatchPlayers,
    "events": Events,
    "shots": Shots,
    "player_career": PlayerCareer,
    "tbf_box": TbfBox,
    "tbf_events": TbfEvents,
    "tbf_shots": TbfShots,
}


def polars_schema(model: type[pa.DataFrameModel]) -> dict[str, pl.DataType]:
    return {name: col.dtype.type for name, col in model.to_schema().columns.items()}
