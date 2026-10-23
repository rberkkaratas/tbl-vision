"""Flat CSV files for Tableau Public (which cannot read Parquet or DuckDB) and a FIBA
half-court background image for shot charts.

Every file carries names next to ids, so a workbook needs no joins. Lists (fives) are
written as ' | '-joined names. Shot coordinates for plotting: plot_x = metres across the
court (0-15, sideline to sideline), plot_y = metres from the attacked baseline (0-14).
"""

from __future__ import annotations

import math
from pathlib import Path

import polars as pl

COURT_W, HALF_L = 15.0, 14.0
RIM = (7.5, 1.575)


def export(staged: dict[str, pl.DataFrame], enriched: dict[str, pl.DataFrame], tables: dict[str, pl.DataFrame],
           out: Path) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    teams = staged["teams"].select("team_season_id", team="name", team_code="code")
    opp = staged["teams"].select(opponent_id="team_season_id", opponent="name")
    names = staged["match_players"].group_by("player_id").agg(player=pl.col("name").first())
    m = staged["matches"].select("match_id", "week", "phase", date=pl.col("start_utc").dt.date())
    mt = staged["match_teams"].select("match_id", "team_season_id", "is_home")
    opp_of = mt.join(mt.select("match_id", opponent_id="team_season_id"), on="match_id").filter(
        pl.col("team_season_id") != pl.col("opponent_id")).drop("is_home")
    def pname(col: str) -> pl.DataFrame:  # names under another id column: passer_id -> passer
        return names.rename({"player_id": col, "player": col.removesuffix("_id")})

    s = enriched["shots_enriched"].join(tables["shot_xpts"].select("match_id", "action_number", "family", "transition",
                                                                   "pts", "xfg", "xpts"), on=["match_id", "action_number"])
    cp = tables["clutch_possessions"].select("match_id", "possession_no", clutch=pl.lit(True))
    shots = (s.join(cp, on=["match_id", "possession_no"], how="left").with_columns(pl.col("clutch").fill_null(False))
             .join(m, on="match_id").join(teams, on="team_season_id").join(opp_of, on=["match_id", "team_season_id"])
             .join(opp, on="opponent_id").join(names, on="player_id", how="left")
             .join(pname("assist_player_id"), on="assist_player_id", how="left")
             .with_columns(plot_x=pl.col("y_m").round(3), plot_y=pl.col("x_m").round(3),
                           period=pl.col("period_ord").replace_strict({1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4"},
                                                                      default="OT", return_dtype=pl.String),
                           transition=pl.col("transition").cast(pl.Boolean))
             .select("match_id", "date", "week", "team", "opponent", "is_home", "player", "period", "clock_s",
                     "shot_type", "sub_type", "family", "zone", "distance_m", "plot_x", "plot_y", "made", "pts",
                     "xfg", "xpts", "assisted", "assist_player", "blocked", "second_chance", "transition",
                     "from_turnover", "fouled", "possession_elapsed_s", "margin_before", "clutch", "location_imputed"))
    files = {
        "shots": shots,
        "player_shooting": tables["player_shooting"].join(names, on="player_id").join(teams, on="team_season_id"),
        "player_zone_shooting": tables["player_zone_shooting"].join(names, on="player_id").join(teams, on="team_season_id"),
        "team_shooting": tables["team_shooting"].join(teams, on="team_season_id"),
        "pairs": tables["pairs"].rename({"player_a": "player_a_id", "player_b": "player_b_id"})
                                .join(pname("player_a_id"), on="player_a_id").join(pname("player_b_id"), on="player_b_id")
                                .join(teams, on="team_season_id"),
        "assist_pairs": tables["assist_pairs"].join(pname("passer_id"), on="passer_id")
                                              .join(pname("scorer_id"), on="scorer_id").join(teams, on="team_season_id"),
        "score_timeline": tables["score_timeline"].join(m, on="match_id").join(
            mt.filter(pl.col("is_home")).select("match_id", home_id="team_season_id"), on="match_id").join(
            teams.rename({"team_season_id": "home_id", "team": "home", "team_code": "home_code"}), on="home_id").join(
            mt.filter(~pl.col("is_home")).select("match_id", away_id="team_season_id"), on="match_id").join(
            teams.rename({"team_season_id": "away_id", "team": "away", "team_code": "away_code"}), on="away_id").join(
            names, on="player_id", how="left").with_columns(minute=(pl.col("elapsed_s") / 60).round(2)),
        "game_flow": tables["game_flow"].join(m, on="match_id").join(teams, on="team_season_id").join(opp, on="opponent_id"),
        "runs": tables["runs"].join(m, on="match_id").join(teams, on="team_season_id"),
        "clutch_team": tables["clutch_team"].join(teams, on="team_season_id"),
        "clutch_player": tables["clutch_player"].join(names, on="player_id").join(teams, on="team_season_id"),
    }
    for name, df in files.items():
        df.write_csv(out / f"{name}.csv")
    court_image(out / "court.png")
    from .workbook import build_workbook

    build_workbook(out, out / f"{out.name}.twb")
    return {k: v.height for k, v in files.items()}


def court_image(path: Path, px_per_m: int = 100) -> None:
    """FIBA half court, baseline at the bottom: x 0-15 m (image width), y 0-14 m (height)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Arc, Circle, Rectangle

    fig = plt.figure(figsize=(COURT_W * px_per_m / 100, HALF_L * px_per_m / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, COURT_W)
    ax.set_ylim(0, HALF_L)
    ax.axis("off")
    line = {"color": "#3a3a3a", "lw": 2, "fill": False}
    ax.add_patch(Rectangle((0, 0), COURT_W, HALF_L, **line))  # boundary + half-court line on top
    ax.add_patch(Rectangle((RIM[0] - 2.45, 0), 4.9, 5.8, **line))  # restricted area ("paint")
    ax.add_patch(Arc((RIM[0], 5.8), 3.6, 3.6, theta1=0, theta2=180, **line))  # free-throw circle
    ax.add_patch(Arc((RIM[0], 5.8), 3.6, 3.6, theta1=180, theta2=360, ls="--", **line))
    ax.add_patch(Arc(RIM, 2.5, 2.5, theta1=0, theta2=180, **line))  # no-charge semicircle 1.25 m
    ax.add_patch(Circle(RIM, 0.225, **line))  # ring
    ax.plot([RIM[0] - 0.9, RIM[0] + 0.9], [1.2, 1.2], color="#3a3a3a", lw=3)  # backboard
    # three-point line: corners 0.90 m from the sidelines up to where they meet the 6.75 m arc
    corner_y = RIM[1] + math.sqrt(6.75 ** 2 - (RIM[0] - 0.9) ** 2)
    ax.plot([0.9, 0.9], [0, corner_y], color="#3a3a3a", lw=2)
    ax.plot([COURT_W - 0.9, COURT_W - 0.9], [0, corner_y], color="#3a3a3a", lw=2)
    a = math.degrees(math.acos((RIM[0] - 0.9) / 6.75))
    ax.add_patch(Arc(RIM, 13.5, 13.5, theta1=a, theta2=180 - a, **line))
    ax.add_patch(Arc((RIM[0], HALF_L), 3.6, 3.6, theta1=180, theta2=360, **line))  # centre circle
    fig.savefig(path, transparent=True)
    plt.close(fig)
