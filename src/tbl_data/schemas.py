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
    "oreb", "dreb", "reb", "ast", "tov", "stl", "blk", "fga_blocked", "pf", "fouls_drawn",
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
    fga_blocked: Int = Count()
    pf: Int = Count()  # all player fouls incl. technical/unsportsmanlike/disqualifying (FIBA 5-foul count)
    fouls_drawn: Int = Count()
    plus_minus: Int = Opt()
    eff: Int  # FIBA efficiency; team rows include team rebounds and subtract team turnovers


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
    coach_bench_fouls: Int = Count()  # coach/bench technicals + coach disqualifying; not in pf
    fouls_total: Int = Count()  # pf + coach_bench_fouls
    pts_off_tov: Int = Count()
    pts_second_chance: Int = Count()
    pts_fast_break: Int = Count()
    pts_paint: Int = Count()
    pts_bench: Int = Count()
    biggest_lead: Int = Count()
    biggest_run: Int = Count()
    lead_changes: Int = Count()
    times_tied: Int = Count()  # raw Genius counter; 1-2 above a recount in some games
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
    career_season: str  # e.g. "2012-2013", "UA-2010-2011" (national team); not the hive season
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
    fga_blocked: Int = Count()
    pf: Int = Count()
    fouls_drawn: Int = Count()
    eff: Int  # FIBA efficiency (TBF playerEfficiency)
    shooting_missing: pl.Boolean  # legacy points-only row: no shot splits, seconds is a placeholder

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


# -- enriched layer --------------------------------------------------------------------
Lineup = pl.List(pl.Int32)


class EventsEnriched(Events):
    """Staged events in canonical order with lineups and possessions."""

    seq: Int = pa.Field(ge=1)  # canonical order within the match
    possession_no: Int = Opt()
    offense_team_id: Int = Opt()
    home_lineup: Lineup
    away_lineup: Lineup
    ft_trip_no: Int = Opt()
    order_override: pl.Boolean  # moved by overrides/{match_id}.json

    class Config:
        strict = True
        unique = ["match_id", "seq"]


class Stints(pa.DataFrameModel):
    match_id: Int
    team_season_id: Int
    stint_no: Int
    period_ord: Int
    start_seq: Int
    start_s: pl.Float64 = Count()
    end_s: pl.Float64 = Count()
    seconds: pl.Float64 = Count()
    lineup: Lineup
    pts_for: Int = Count()
    pts_against: Int = Count()
    poss_for: Int = Count()
    poss_against: Int = Count()

    class Config:
        strict = True


class Possessions(pa.DataFrameModel):
    match_id: Int
    possession_no: Int = pa.Field(ge=1)
    team_season_id: Int  # offense
    period_ord: Int
    start_seq: Int
    end_seq: Int
    start_s: pl.Float64 = Count()
    end_s: pl.Float64 = Count()
    duration_s: pl.Float64 = Count()
    start_type: str = pa.Field(isin=["period_start", "after_made_fg", "after_made_ft", "after_defensive_rebound",
                                     "after_steal", "after_turnover", "jumpball", "unsportsmanlike_foul", "inferred"])
    end_type: str = pa.Field(isin=["made_fg", "made_ft", "defensive_rebound", "turnover", "period_end",
                                   "jumpball", "unsportsmanlike_foul", "inferred", "game_end"])
    pts: Int = Count()
    pts_against: Int = Count()  # e.g. technical free throws by the defense
    fga: Int = Count()
    fg3a: Int = Count()
    fta: Int = Count()
    oreb: Int = Count()
    tov: Int = Count()

    class Config:
        strict = True
        unique = ["match_id", "possession_no"]


class ShotsEnriched(Shots):
    seq: Int = Opt()
    elapsed_s: pl.Float64 = Opt()
    clock_s: pl.Float64 = Opt()
    x_m: pl.Float64 = pa.Field(ge=0, le=28)  # metres from the attacked baseline
    y_m: pl.Float64 = pa.Field(ge=0, le=15)
    distance_m: pl.Float64 = Count()  # from the basket centre
    zone: str = pa.Field(isin=["rim", "paint", "midrange", "corner_3", "above_break_3", "heave"])
    location_imputed: pl.Boolean  # tip-ins logged at Genius' default spot
    assisted: pl.Boolean
    assist_player_id: Int = Opt()
    blocked: pl.Boolean
    block_player_id: Int = Opt()
    second_chance: pl.Boolean
    fast_break: pl.Boolean
    from_turnover: pl.Boolean
    in_paint: pl.Boolean
    fouled: pl.Boolean
    possession_no: Int = Opt()
    possession_elapsed_s: pl.Float64 = Opt()
    margin_before: Int = Opt()  # shooting team's lead before the shot
    offense_lineup: Lineup
    defense_lineup: Lineup

    class Config:
        strict = True
        unique = ["match_id", "action_number"]


class FtTrips(pa.DataFrameModel):
    match_id: Int
    trip_no: Int
    team_season_id: Int
    shooter_id: Int
    n_awarded: Int = pa.Field(ge=1, le=3)
    attempts: Int = pa.Field(ge=1)
    made: Int = Count()
    trip_type: str = pa.Field(isin=["and_one", "shooting_2pt", "shooting_3pt", "shooting", "penalty",
                                    "technical", "unsportsmanlike", "unknown"])
    foul_action: Int = Opt()
    foul_sub_type: str = Opt()
    fouler_id: Int = Opt()
    period_ord: Int
    elapsed_s: pl.Float64
    first_seq: Int
    last_seq: Int
    complete: pl.Boolean
    possession_no: Int = Opt()

    class Config:
        strict = True
        unique = ["match_id", "trip_no"]


class EnrichIssues(pa.DataFrameModel):
    match_id: Int
    source: str = pa.Field(isin=["order", "lineup", "possession", "override"])
    issue: str
    action_number: Int = Opt()
    seq: Int = Opt()
    team_season_id: Int = Opt()
    player_id: Int = Opt()
    period_label: str = Opt()
    clock: str = Opt()
    note: str = Opt()

    class Config:
        strict = True


ENRICHED_TABLES: dict[str, type[pa.DataFrameModel]] = {
    "events_enriched": EventsEnriched,
    "stints": Stints,
    "possessions": Possessions,
    "shots_enriched": ShotsEnriched,
    "ft_trips": FtTrips,
    "enrich_issues": EnrichIssues,
}
