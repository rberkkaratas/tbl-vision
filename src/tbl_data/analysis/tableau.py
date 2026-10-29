"""Flat CSV files for Tableau Public (which cannot read Parquet or DuckDB) and a FIBA
half-court background image for shot charts.

Every file carries names next to ids, so a workbook needs no joins. Lists (fives) are
written as ' | '-joined names. Shot coordinates for plotting: plot_x = metres across the
court (0-15, sideline to sideline), plot_y = metres from the attacked baseline (0-14).
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import polars as pl

from .assets import TBL, team_theme
from .glossary import annotate
from .presentation import augment, matchup_views, team_codes
from .profiles import player_names

COURT_W, HALF_L = 15.0, 14.0
RIM = (7.5, 1.575)
START_LABELS = {"after_defensive_rebound": "Savunma ribaundu sonrası", "after_made_fg": "Sayı sonrası",
                "after_steal": "Top çalma sonrası", "after_turnover": "Ölü top kaybı sonrası",
                "after_made_ft": "Serbest atış sonrası", "period_start": "Periyot başı", "jumpball": "Hava atışı",
                "unsportsmanlike_foul": "Sportmenlik dışı faul", "inferred": "Belirsiz (veri boşluğu)"}
# (column, label, side, +1 if higher is better for the team)
FOUR_FACTORS = [("efg", "eFG%", "Hücum", 1), ("tov_pct", "Top kaybı %", "Hücum", -1),
                ("orb_pct", "Hücum ribaundu %", "Hücum", 1), ("ft_rate", "Serbest atış oranı", "Hücum", 1),
                ("opp_efg", "eFG%", "Savunma", -1), ("opp_tov_pct", "Top kaybı %", "Savunma", 1),
                ("drb_pct", "Savunma ribaundu %", "Savunma", 1), ("opp_ft_rate", "Serbest atış oranı", "Savunma", -1)]


def export(staged: dict[str, pl.DataFrame], enriched: dict[str, pl.DataFrame], tables: dict[str, pl.DataFrame],
           out: Path, metrics: dict | None = None, logos: Path | None = None) -> dict[str, int]:
    """logos: the folder assets.fetch_logos filled (team colours come from the logos; without it, from TBF)."""
    out.mkdir(parents=True, exist_ok=True)
    teams = staged["teams"].select("team_season_id", team="name", team_code="code")
    opp = staged["teams"].select(opponent_id="team_season_id", opponent="name")
    names = player_names(staged)
    m = staged["matches"].select("match_id", "week", "phase", date=pl.col("start_utc").dt.date())
    # forfeits: game_flow keeps the played score, official_pts is the 20-0 result
    forfeit = pl.concat([staged["matches"].select("match_id", "forfeit", team_season_id=f"{side}_team_id",
                                                  official_pts=f"{side}_score") for side in ("home", "away")])
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
        "game_flow": tables["game_flow"].join(m, on="match_id").join(forfeit, on=["match_id", "team_season_id"])
                     .join(teams, on="team_season_id").join(opp, on="opponent_id"),
        "runs": tables["runs"].join(m, on="match_id").join(teams, on="team_season_id"),
        "clutch_team": tables["clutch_team"].join(teams, on="team_season_id"),
        "team_profiles": tables["team_profiles"].join(teams, on="team_season_id", how="left").with_columns(
            team=pl.col("team").fill_null("Lig ortalaması"), team_code=pl.coalesce("team_code", "team")),
        "four_factors": four_factors(tables["team_profiles"]).join(teams, on="team_season_id"),
        "possession_starts": tables["possession_starts"].join(teams, on="team_season_id").with_columns(
            start=pl.col("start_type").replace_strict(START_LABELS, default=pl.col("start_type")),
            side=pl.col("side").replace_strict({"offense": "Hücum", "defense": "Savunma"})),
        "player_profiles": tables["player_profiles"],
        "player_percentiles": tables["player_percentiles"],
        "clutch_player": tables["clutch_player"].join(names, on="player_id").join(teams, on="team_season_id"),
        "stat_reliability": tables["stat_reliability"],
        "xpts_calibration": tables["xpts_calibration"],
        "prior_backtest": tables["prior_backtest"],
        "player_stable": tables["player_stable"].join(names, on="player_id").join(teams, on="team_season_id"),
    }
    codes = team_codes(staged["teams"])
    files = augment(files, codes)
    display = {(t, p): n for t, p, n in files["player_profiles"].select("team_season_id", "player_id", "player").iter_rows()}
    files |= matchup_views(tables, dict(staged["teams"].select("team_season_id", "name").iter_rows()), codes, names,
                           metrics, display)
    files = annotate(files)  # what each metric means, for the tooltips
    theme = team_theme(staged["teams"], codes, logos)
    files["team_theme"] = pl.concat([theme, pl.DataFrame([{"team": TBL, "team_short": TBL, "color": "#1b2430",
                                                           "color_light": "#b4bcc5", "color_dark": "#1b2430",
                                                           "logo": f"{TBL}.png"}])])
    # the header block is an area chart over two points per team, so it fills its zone in the team's colour
    files["theme_band"] = pl.concat([theme.select("team", x=pl.lit(v), y=pl.lit(1.0)) for v in (0.0, 1.0)])
    if logos is not None:  # logos embedded as shapes; the wide league logo is the header image
        (out / "logos").mkdir(exist_ok=True)
        for f in logos.glob("*.png"):
            shutil.copy2(f, out / "logos" / f.name)
        if (logos.parent / f"{TBL}_wide.png").exists():
            shutil.copy2(logos.parent / f"{TBL}_wide.png", out / f"{TBL}.png")
    for name, df in files.items():
        df.write_csv(out / f"{name}.csv")
    court_image(out / "court.png")
    from .workbook import build_workbook

    build_workbook(out, out / f"{out.name}.twb")
    return {k: v.height for k, v in files.items()}


def four_factors(teams: pl.DataFrame) -> pl.DataFrame:
    """Long format: team x factor, with the league value and how much better (+) or worse the team is."""
    league = teams.filter(pl.col("is_league")).row(0, named=True)
    t = teams.filter(~pl.col("is_league"))
    return pl.concat([t.select("team_season_id", value=pl.col(c)).with_columns(
        factor=pl.lit(label), side=pl.lit(side), league=pl.lit(league[c]),
        better=((pl.col("value") - league[c]) * sign).round(3)) for c, label, side, sign in FOUR_FACTORS])


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
