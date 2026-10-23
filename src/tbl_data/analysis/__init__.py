"""staged + enriched -> analysis tables: xPTS, shooting summaries, pairs, game flow, clutch."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl
import structlog

from . import flow, pairs, xpts

log = structlog.get_logger()


@dataclass
class Analysis:
    tables: dict[str, pl.DataFrame]
    metrics: dict


def analyze(staged: dict[str, pl.DataFrame], enriched: dict[str, pl.DataFrame]) -> Analysis:
    mt, ev = staged["match_teams"], enriched["events_enriched"]
    x = xpts.fit(enriched["shots_enriched"], enriched["possessions"])
    timeline = flow.score_timeline(ev, mt)
    games, runs = flow.game_flow(timeline, mt)
    clutch_poss, clutch_team, clutch_player = flow.clutch(enriched["possessions"], timeline, ev, mt)
    tables = {
        "shot_xpts": x.shots.select("match_id", "action_number", "team_season_id", "player_id", "family",
                                    "transition", "value", "pts", "xfg", "xpts"),
        "xpts_coefficients": x.coefficients,
        "player_shooting": shooting(x.shots, ["team_season_id", "player_id"]),
        "player_zone_shooting": zone_shooting(x.shots),
        "team_shooting": team_shooting(x.shots, mt),
        "pairs": pairs.pair_on_court(enriched["stints"]),
        "assist_pairs": pairs.assist_pairs(enriched["shots_enriched"]),
        "score_timeline": timeline,
        "game_flow": games,
        "runs": runs,
        "clutch_possessions": clutch_poss.select("match_id", "possession_no", "team_season_id", "period_ord",
                                                 "remaining_s", "margin_offense", "pts", "end_type"),
        "clutch_team": clutch_team,
        "clutch_player": clutch_player,
    }
    log.info("analyze.done", **{k: v.height for k, v in tables.items()}, **{k: x.metrics[k] for k in
             ("log_loss", "log_loss_zone_baseline", "xpts_per_shot", "pts_per_shot")})
    return Analysis(tables, {"xpts": x.metrics})


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
