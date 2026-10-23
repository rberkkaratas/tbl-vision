"""Two-player views: how a team does with both on court vs one without the other, and who
assists whom.

On-court numbers come from stints (seconds, points, possessions of each unchanged five),
so they include technical free throws and use the stint's possession credit. Ratings are
per 100 possessions; with 6-7 games per team most pairs have few possessions, so the
possession counts are kept next to every rating and nothing is shrunk or hidden.
"""

from __future__ import annotations

from itertools import combinations

import polars as pl

ON_COLS = ["seconds", "pts_for", "pts_against", "poss_for", "poss_against"]


def _ratings(df: pl.DataFrame, p: str = "") -> pl.DataFrame:
    """ortg/drtg/net per 100 possessions for columns with prefix p; null without possessions."""
    def per100(pts: str, poss: str) -> pl.Expr:
        return pl.when(pl.col(poss) > 0).then((pl.col(pts) * 100 / pl.col(poss)).round(1))

    return df.with_columns(per100(f"{p}pts_for", f"{p}poss_for").alias(f"{p}ortg"),
                           per100(f"{p}pts_against", f"{p}poss_against").alias(f"{p}drtg")).with_columns(
        (pl.col(f"{p}ortg") - pl.col(f"{p}drtg")).round(1).alias(f"{p}net"))


def player_on_court(stints: pl.DataFrame) -> pl.DataFrame:
    """team, player -> on-court totals for the season."""
    return stints.explode("lineup").rename({"lineup": "player_id"}).group_by("team_season_id", "player_id").agg(
        games=pl.col("match_id").n_unique(), *[pl.col(c).sum() for c in ON_COLS])


def pair_on_court(stints: pl.DataFrame) -> pl.DataFrame:
    """team, player_a < player_b -> together, a without b, b without a."""
    rows = []
    for s in stints.iter_rows(named=True):
        for a, b in combinations(sorted(s["lineup"]), 2):
            rows.append({"match_id": s["match_id"], "team_season_id": s["team_season_id"], "player_a": a,
                         "player_b": b, **{c: s[c] for c in ON_COLS}})
    together = pl.DataFrame(rows).group_by("team_season_id", "player_a", "player_b").agg(
        games=pl.col("match_id").n_unique(), *[pl.col(c).sum() for c in ON_COLS])
    on = player_on_court(stints).drop("games")
    out = together
    for who, other in (("a", "b"), ("b", "a")):
        o = on.rename({"player_id": f"player_{who}", **{c: f"{who}_on_{c}" for c in ON_COLS}})
        out = out.join(o, on=["team_season_id", f"player_{who}"]).with_columns(
            [(pl.col(f"{who}_on_{c}") - pl.col(c)).alias(f"{who}_without_{other}_{c}") for c in ON_COLS]
        ).drop([f"{who}_on_{c}" for c in ON_COLS])
    out = _ratings(out)
    for who, other in (("a", "b"), ("b", "a")):
        out = _ratings(out, f"{who}_without_{other}_")
    return out.with_columns(minutes=(pl.col("seconds") / 60).round(1), poss=pl.col("poss_for") + pl.col("poss_against"))


def assist_pairs(shots: pl.DataFrame) -> pl.DataFrame:
    """assister -> scorer: assisted baskets and their points, by zone."""
    a = shots.filter(pl.col("assisted") & pl.col("assist_player_id").is_not_null()).with_columns(
        pts=pl.when(pl.col("shot_type") == "3pt").then(3).otherwise(2))
    return a.group_by("team_season_id", passer_id="assist_player_id", scorer_id="player_id").agg(
        assists=pl.len(), pts=pl.col("pts").sum(), games=pl.col("match_id").n_unique(),
        **{f"at_{z}": (pl.col("zone") == z).sum() for z in ("rim", "paint", "midrange", "corner_3", "above_break_3")})
