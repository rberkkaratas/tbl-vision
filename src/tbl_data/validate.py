"""Cross-source checks on staged tables. Each check returns failing rows (empty = pass)."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from .schemas import BOX_STATS


@dataclass
class Check:
    name: str
    description: str
    checked: int
    failures: pl.DataFrame
    blocking: bool = True  # False: known, explained difference that is reported only

    @property
    def ok(self) -> bool:
        return self.failures.height == 0


def run_all(t: dict[str, pl.DataFrame], drift: pl.DataFrame, unmapped: list[dict]) -> list[Check]:
    return [
        box_tbf_vs_genius(t),
        minutes_tbf_vs_genius(t),
        player_mapping(t, unmapped),
        tbf_quirks(t),
        team_points(t),
        period_scores(t),
        shots_vs_fga(t),
        tbf_shots_vs_genius(t),
        tbf_events_subset(t),
        career_points(t),
        Check("schema_drift", "no unseen source fields", drift.height, drift),
    ]


COUNTING = tuple(c for c in BOX_STATS if c != "seconds")


def _box_join(t) -> pl.DataFrame:
    g = t["match_players"].select("match_id", "player_id", *BOX_STATS)
    b = t["tbf_box"].select("match_id", "player_id", *BOX_STATS)
    return g.join(b, on=["match_id", "player_id"], suffix="_tbf")


def box_tbf_vs_genius(t) -> Check:
    """Every counting stat of every player equal in Genius and TBF."""
    j = _box_join(t)
    diffs = [
        j.filter(pl.col(c).ne_missing(pl.col(f"{c}_tbf")))
        .select("match_id", "player_id", pl.lit(c).alias("stat"),
                pl.col(c).cast(pl.Int64).alias("genius"), pl.col(f"{c}_tbf").cast(pl.Int64).alias("tbf"))
        for c in COUNTING
    ]
    return Check("box_tbf_vs_genius", f"{len(COUNTING)} counting stats x player equal in both sources",
                 j.height * len(COUNTING), pl.concat(diffs))


def minutes_tbf_vs_genius(t) -> Check:
    """Playing time within 1 s (sources round differently). Larger gaps are TBF
    crediting time to the wrong teammate (team totals still agree); Genius is kept."""
    j = _box_join(t).with_columns(diff=pl.col("seconds") - pl.col("seconds_tbf"))
    return Check("minutes_tbf_vs_genius", "player seconds within 1 s of TBF (informational)", j.height,
                 j.filter(pl.col("diff").abs() > 1).select("match_id", "player_id", "seconds", "seconds_tbf", "diff"),
                 blocking=False)


def player_mapping(t, unmapped) -> Check:
    """Every Genius player has a TBF id, every TBF box row a Genius row."""
    g, b = t["match_players"], t["tbf_box"]
    only_tbf = b.join(g, on=["match_id", "player_id"], how="anti").select(
        "match_id", "team_season_id", "jersey", "name", pl.lit("tbf_only").alias("side"))
    only_g = pl.DataFrame(unmapped, schema={"match_id": pl.Int32, "team_season_id": pl.Int32,
                                            "jersey": pl.String, "name": pl.String}
                          ).with_columns(pl.lit("genius_only").alias("side"))
    return Check("player_mapping", "Genius (team, jersey) <-> TBF person id is 1:1", g.height + b.height,
                 pl.concat([only_tbf, only_g]))


def tbf_quirks(t) -> Check:
    """Our reading of TBF fields: fgm = 2P made + 3P made, reb = oreb + dreb."""
    b = t["tbf_box"]
    bad = b.filter((pl.col("fgm") != pl.col("fg2m") + pl.col("fg3m"))
                   | (pl.col("fga") != pl.col("fg2a") + pl.col("fg3a"))
                   | (pl.col("reb") != pl.col("oreb") + pl.col("dreb"))
                   | (pl.col("pts") != 2 * pl.col("fg2m") + 3 * pl.col("fg3m") + pl.col("ftm")))
    return Check("tbf_quirks", "TBF box fields add up under our field mapping", b.height,
                 bad.select("match_id", "player_id", "pts", "fgm", "fg2m", "fg3m", "ftm"))


def team_points(t) -> Check:
    """Sum of player points = Genius team points = TBF final score."""
    p = t["match_players"].group_by("match_id", "team_season_id").agg(player_pts=pl.col("pts").sum())
    m = t["matches"]
    final = pl.concat([
        m.select("match_id", pl.col("home_team_id").alias("team_season_id"), pl.col("home_score").alias("final")),
        m.select("match_id", pl.col("away_team_id").alias("team_season_id"), pl.col("away_score").alias("final")),
    ])
    j = (t["match_teams"].select("match_id", "team_season_id", pl.col("pts").alias("team_pts"))
         .join(p, on=["match_id", "team_season_id"], how="full", coalesce=True)
         .join(final, on=["match_id", "team_season_id"], how="full", coalesce=True))
    bad = j.filter((pl.col("team_pts") != pl.col("player_pts")) | (pl.col("team_pts") != pl.col("final"))
                   | pl.any_horizontal(pl.col("team_pts", "player_pts", "final").is_null()))
    return Check("team_points", "player sum = team total = TBF final score", j.height, bad)


def period_scores(t) -> Check:
    """Points per period rebuilt from the play-by-play sum to the final score."""
    s = t["period_scores"].group_by("match_id", "team_season_id").agg(pbp_pts=pl.col("pts").sum())
    j = t["match_teams"].select("match_id", "team_season_id", "pts").join(s, on=["match_id", "team_season_id"], how="left")
    return Check("period_scores", "sum of per-period points (from pbp) = final", j.height,
                 j.filter(pl.col("pts").ne_missing(pl.col("pbp_pts"))))


def shots_vs_fga(t) -> Check:
    """Genius shot list has exactly one entry per field goal attempt."""
    s = t["shots"].group_by("match_id", "team_season_id").agg(
        shots=pl.len(), made=pl.col("made").sum(), threes=(pl.col("shot_type") == "3pt").sum())
    j = t["match_teams"].select("match_id", "team_season_id", "fga", "fgm", "fg3a").join(
        s, on=["match_id", "team_season_id"], how="left")
    bad = j.filter((pl.col("shots").ne_missing(pl.col("fga"))) | (pl.col("made").ne_missing(pl.col("fgm")))
                   | (pl.col("threes").ne_missing(pl.col("fg3a"))))
    return Check("shots_vs_fga", "shots = FGA, made = FGM, 3pt shots = 3PA (per team)", j.height, bad)


def tbf_shots_vs_genius(t) -> Check:
    """TBF shot chart is a copy of Genius; report matches where counts differ."""
    g = t["shots"].group_by("match_id").agg(genius=pl.len())
    b = t["tbf_shots"].group_by("match_id").agg(tbf=pl.len())
    j = g.join(b, on="match_id", how="full", coalesce=True).with_columns(
        pl.col("genius", "tbf").fill_null(0)).with_columns(diff=pl.col("genius") - pl.col("tbf"))
    return Check("tbf_shots_vs_genius", "TBF shot chart count = Genius shots (informational)", j.height,
                 j.filter(pl.col("diff") != 0).sort("diff", descending=True), blocking=False)


def tbf_events_subset(t) -> Check:
    """Every TBF game-flow action number exists in the Genius play-by-play."""
    j = t["tbf_events"].join(t["events"].select("match_id", "action_number"), on=["match_id", "action_number"], how="anti")
    return Check("tbf_events_subset", "TBF game-flow actions are a subset of Genius pbp", t["tbf_events"].height,
                 j.select("match_id", "action_number", "text"))


def career_points(t) -> Check:
    """TBF career rows: pts = 2*fg2m + 3*fg3m + ftm (proves fieldGoalsMade = 2P made).

    Known exceptions: national-team ('UA-' seasons) rows carry points only, and a few
    pre-2017 club rows are incomplete at the source.
    """
    c = t["player_career"]
    bad = c.filter(pl.col("pts") != 2 * pl.col("fg2m") + 3 * pl.col("fg3m") + pl.col("ftm"))
    return Check("career_points", "career pts = 2*2PM + 3*3PM + FTM (informational)", c.height,
                 bad.select("player_id", "career_season", "competition", "pts", "fg2m", "fg3m", "ftm"), blocking=False)


def report(checks: list[Check]) -> pl.DataFrame:
    return pl.DataFrame([{"check": c.name, "ok": c.ok, "blocking": c.blocking, "checked": c.checked,
                          "failures": c.failures.height, "description": c.description} for c in checks])
