"""staged + enriched -> analysis tables: xPTS, shooting, team and player profiles, pairs, game flow, clutch,
and how far each player rate can be trusted (stabilization, rates padded with last season)."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl
import structlog

from . import flow, pairs, profiles, stability, xpts

log = structlog.get_logger()


@dataclass
class Analysis:
    tables: dict[str, pl.DataFrame]
    metrics: dict


@dataclass
class Prior:
    """Last season: its enriched layer (shots, possessions) and its analysis layer."""
    season: str
    enriched: dict[str, pl.DataFrame]
    analysis: dict[str, pl.DataFrame]


BACKTEST_MIN_TEAM_GAMES = 16  # tune the prior-season weights only on a season this long


def analyze(staged: dict[str, pl.DataFrame], enriched: dict[str, pl.DataFrame], prior: Prior | None = None,
            season: str = "") -> Analysis:
    """season: its key ('2026-27'), to find last season in TBF's career table."""
    mt, ev = staged["match_teams"], enriched["events_enriched"]
    x = xpts.fit(enriched["shots_enriched"], enriched["possessions"],
                 (prior.enriched["shots_enriched"], prior.enriched["possessions"]) if prior else None)
    timeline = flow.score_timeline(ev, mt)
    games, runs = flow.game_flow(timeline, mt)
    clutch_poss, clutch_team, clutch_player = flow.clutch(enriched["possessions"], timeline, ev, mt)
    player_shooting = shooting(x.shots, ["team_season_id", "player_id"])
    team_prof, starts = profiles.team_profiles(staged, enriched, x.shots)
    games_played = profiles.player_games(staged, enriched, x.shots)
    player_prof, percentiles = profiles.player_profiles(staged, games_played, player_shooting)
    stab = _stability(staged, games_played, prior, season)
    tables = {
        "shot_xpts": x.shots.select("match_id", "action_number", "team_season_id", "player_id", "family",
                                    "transition", "value", "pts", "xfg", "xpts"),
        "xpts_coefficients": x.coefficients,
        "xpts_calibration": x.calibration,
        "player_shooting": player_shooting,
        "player_zone_shooting": zone_shooting(x.shots),
        "team_shooting": team_shooting(x.shots, mt),
        "team_profiles": team_prof,
        "possession_starts": starts,
        "player_games": games_played,
        "player_profiles": player_prof,
        "player_percentiles": percentiles,
        "pairs": pairs.pair_on_court(enriched["stints"]),
        "assist_pairs": pairs.assist_pairs(enriched["shots_enriched"]),
        "score_timeline": timeline,
        "game_flow": games,
        "runs": runs,
        "clutch_possessions": clutch_poss.select("match_id", "possession_no", "team_season_id", "period_ord",
                                                 "remaining_s", "margin_offense", "pts", "end_type"),
        "clutch_team": clutch_team,
        "clutch_player": clutch_player,
        **stab,
    }
    log.info("analyze.done", **{k: v.height for k, v in tables.items()}, **{k: x.metrics[k] for k in
             ("log_loss", "log_loss_zone_baseline", "xpts_per_shot", "pts_per_shot")})
    return Analysis(tables, {"xpts": x.metrics, "stability": {
        "reliability_from": stab["stat_reliability"]["fitted_on"][0],
        "weights_from": stab["prior_weights"]["fitted_on"][0] if stab["prior_weights"].height else None,
        "prior_season": prior.season if prior else None}})


def _stability(staged: dict[str, pl.DataFrame], games: pl.DataFrame, prior: Prior | None,
               season: str) -> dict[str, pl.DataFrame]:
    """Stabilization points and last-season weights: fitted on this season once it is long
    enough, else taken from last season's analysis; then every player's padded rates."""
    mt, matches = staged["match_teams"], staged["matches"]
    career = stability.career_prior(staged["player_career"], season) if season and "player_career" in staged else None
    pa = prior.analysis if prior else {}
    # refit on this season once it is long enough, and (when last season's full-season fit is
    # there) about as long as last season: half a season pins k down less well than a whole one
    n = mt.group_by("team_season_id").len()["len"].median()
    full = pa["stat_reliability"]["season_games"][0] if "stat_reliability" in pa else 0
    long = n >= max(BACKTEST_MIN_TEAM_GAMES, 0.9 * full)
    if long or "stat_reliability" not in pa:
        rel = stability.reliability(games, matches, profiles.qualify_minutes(mt)).with_columns(fitted_on=pl.lit(season))
    else:
        rel = pa["stat_reliability"]
    if long and career is not None:
        bt, d = stability.backtest(games, matches, career, rel)
        bt, d = bt.with_columns(fitted_on=pl.lit(season)), d.with_columns(fitted_on=pl.lit(season))
    elif "prior_weights" in pa:
        bt, d = pa["prior_backtest"], pa["prior_weights"]
    else:
        bt, d = pl.DataFrame(), pl.DataFrame(schema={"group": pl.String, "d_tbl": pl.Float64, "d_other": pl.Float64,
                                                     "fitted_on": pl.String})
    stable = stability.stable_rates(games, rel, d, career, pa.get("player_games"))
    return {"stat_reliability": rel, "prior_backtest": bt, "prior_weights": d, "player_stable": stable}


def _poe(df: pl.DataFrame) -> pl.DataFrame:
    """Points over expected, per shot, with its standard error under the model."""
    return df.with_columns(
        poe=(pl.col("pts") - pl.col("xpts")).round(2),
        poe_per_shot=((pl.col("pts") - pl.col("xpts")) / pl.col("shots")).round(3),
        poe_se=(pl.col("var").sqrt() / pl.col("shots")).round(3),
        xpts_per_shot=(pl.col("xpts") / pl.col("shots")).round(3),  # shot quality
        pts_per_shot=(pl.col("pts") / pl.col("shots")).round(3),
        fg_pct=(pl.col("fgm") / pl.col("shots")).round(3),
        xfg_pct=(pl.col("xfgm") / pl.col("shots")).round(3),
    ).drop("var")


def _agg() -> list[pl.Expr]:
    return [pl.len().alias("shots"), pl.col("made").sum().alias("fgm"), pl.col("xfg").sum().round(2).alias("xfgm"),
            pl.col("pts").sum(), pl.col("xpts").sum().round(2),
            (pl.col("xfg") * (1 - pl.col("xfg")) * pl.col("value") ** 2).sum().alias("var"),
            (pl.col("shot_type") == "3pt").sum().alias("fg3a"), pl.col("match_id").n_unique().alias("games")]


def shooting(shots: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    return _poe(shots.group_by(by).agg(_agg()))


def zone_shooting(shots: pl.DataFrame) -> pl.DataFrame:
    league = shots.group_by("zone").agg(league_fg_pct=pl.col("made").mean().round(3),
                                        league_pts_per_shot=pl.col("pts").mean().round(3))
    return _poe(shots.group_by("team_season_id", "player_id", "zone").agg(_agg())).join(league, on="zone")


def team_shooting(shots: pl.DataFrame, match_teams: pl.DataFrame) -> pl.DataFrame:
    """Offense (own shots) and defense (opponents' shots) shot quality and shot-making."""
    opp = match_teams.select("match_id", "team_season_id").join(
        match_teams.select("match_id", defense_id="team_season_id"), on="match_id").filter(
        pl.col("team_season_id") != pl.col("defense_id"))
    off = shooting(shots, ["team_season_id"]).with_columns(side=pl.lit("offense"))
    d = shooting(shots.join(opp, on=["match_id", "team_season_id"]).drop("team_season_id")
                 .rename({"defense_id": "team_season_id"}), ["team_season_id"]).with_columns(side=pl.lit("defense"))
    return pl.concat([off, d])
