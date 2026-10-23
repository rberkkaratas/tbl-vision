"""Team and player season profiles.

Team ratings use counted possessions (not the box estimate) and box-score points, which
include technical free throws. Four factors follow Dean Oliver: eFG%, TOV% (turnovers per
possession), ORB% / DRB%, FT rate (FTM / FGA); team rebounds and team turnovers count.

Player rates use what happened while the player was on court (from the lineups):
  USG% = (FGA + 0.44 FTA + TOV) / the team's FGA + 0.44 FTA + TOV on court (averages 20%)
  AST% = assists / teammates' field goals made on court
  ORB% / DRB% = player rebounds / available rebounds on court (team + opponent)
  STL% = steals / opponent possessions on court; BLK% = blocks / opponent 2PA on court
  on/off = team net rating with the player on court minus with the player off
Percentiles are among players with at least QUALIFY_MIN minutes.
"""

from __future__ import annotations

from datetime import date

import polars as pl

QUALIFY_MIN = 100
SEASON_START = date(2026, 10, 1)
ZONES = ["rim", "paint", "midrange", "corner_3", "above_break_3"]
BOX = ["pts", "fgm", "fga", "fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast", "tov", "stl",
       "blk", "pf", "fouls_drawn"]


def _div(a: pl.Expr, b: pl.Expr, scale: float = 1.0, digits: int = 3) -> pl.Expr:
    return pl.when(b > 0).then((a * scale / b).round(digits))


def team_profiles(staged: dict[str, pl.DataFrame], enriched: dict[str, pl.DataFrame],
                  shots: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """-> (one row per team plus a league row, points per possession by how possessions start)."""
    mt, poss = staged["match_teams"], enriched["possessions"]
    length = staged["matches"].select("match_id", game_s=2400 + 300 * pl.col("n_overtimes"))
    n = poss.group_by("match_id", "team_season_id").agg(poss=pl.len())
    g = mt.select("match_id", "team_season_id", *BOX).join(n, on=["match_id", "team_season_id"]).join(length, on="match_id")
    opp = g.select("match_id", pl.col("team_season_id").alias("opp_id"), *[pl.col(c).alias(f"opp_{c}") for c in [*BOX, "poss"]])
    g = g.join(opp, on="match_id").filter(pl.col("team_season_id") != pl.col("opp_id"))
    sums = [*BOX, "poss", "game_s", *[f"opp_{c}" for c in [*BOX, "poss"]]]

    def profile(df: pl.DataFrame) -> pl.DataFrame:
        return df.with_columns(
            pace=_div((pl.col("poss") + pl.col("opp_poss")) / 2, pl.col("game_s"), 2400, 1),
            ortg=_div(pl.col("pts"), pl.col("poss"), 100, 1), drtg=_div(pl.col("opp_pts"), pl.col("opp_poss"), 100, 1),
            efg=_div(pl.col("fgm") + 0.5 * pl.col("fg3m"), pl.col("fga")),
            tov_pct=_div(pl.col("tov"), pl.col("poss")),
            orb_pct=_div(pl.col("oreb"), pl.col("oreb") + pl.col("opp_dreb")),
            ft_rate=_div(pl.col("ftm"), pl.col("fga")),
            opp_efg=_div(pl.col("opp_fgm") + 0.5 * pl.col("opp_fg3m"), pl.col("opp_fga")),
            opp_tov_pct=_div(pl.col("opp_tov"), pl.col("opp_poss")),
            drb_pct=_div(pl.col("dreb"), pl.col("dreb") + pl.col("opp_oreb")),
            opp_ft_rate=_div(pl.col("opp_ftm"), pl.col("opp_fga")),
            fg3a_rate=_div(pl.col("fg3a"), pl.col("fga")), ast_rate=_div(pl.col("ast"), pl.col("fgm")),
            stl_pct=_div(pl.col("stl"), pl.col("opp_poss")), blk_pct=_div(pl.col("blk"), pl.col("opp_fg2a")),
            ts=_div(pl.col("pts"), 2 * (pl.col("fga") + 0.44 * pl.col("fta"))),
            pts_pg=_div(pl.col("pts"), pl.col("games"), digits=1), opp_pts_pg=_div(pl.col("opp_pts"), pl.col("games"), digits=1),
        ).with_columns(net=(pl.col("ortg") - pl.col("drtg")).round(1))

    agg = [pl.len().alias("games"), (pl.col("pts") > pl.col("opp_pts")).sum().alias("wins"),
           *[pl.col(c).sum() for c in sums]]
    teams = profile(g.group_by("team_season_id").agg(agg))
    league = profile(g.select(agg).with_columns(team_season_id=pl.lit(None, pl.Int32)))
    # shot diet: share of attempts and FG% by zone, shot quality (xPTS per shot)
    diet_exprs = [*[(pl.col("zone") == z).mean().round(3).alias(f"share_{z}") for z in ZONES],
                  *[pl.col("made").filter(pl.col("zone") == z).mean().round(3).alias(f"fg_{z}") for z in ZONES],
                  pl.col("xpts").mean().round(3).alias("xpts_per_shot"),
                  pl.col("transition").cast(pl.Float64).mean().round(3).alias("transition_share")]
    diet = shots.group_by("team_season_id").agg(diet_exprs)
    league_diet = shots.select(diet_exprs)
    out = pl.concat([teams.join(diet, on="team_season_id", how="left"),
                     pl.concat([league, league_diet], how="horizontal")], how="diagonal_relaxed")
    out = out.with_columns(losses=pl.col("games") - pl.col("wins"), is_league=pl.col("team_season_id").is_null())

    # how possessions start, and what they are worth, for and against
    p = poss.join(mt.select("match_id", "team_season_id", "is_home"), on=["match_id", "team_season_id"]).join(
        mt.select("match_id", defense_id="team_season_id"), on="match_id").filter(
        pl.col("defense_id") != pl.col("team_season_id"))
    start = pl.concat([
        p.group_by("team_season_id", "start_type").agg(poss=pl.len(), pts=pl.col("pts").sum()).with_columns(side=pl.lit("offense")),
        p.group_by(pl.col("defense_id").alias("team_season_id"), "start_type").agg(poss=pl.len(), pts=pl.col("pts").sum())
        .with_columns(side=pl.lit("defense")),
    ]).with_columns(ppp=_div(pl.col("pts"), pl.col("poss")),
                    share=(pl.col("poss") / pl.col("poss").sum().over("team_season_id", "side")).round(3))
    lg = p.group_by("start_type").agg(league_ppp=_div(pl.col("pts").sum(), pl.len()),
                                      league_share=(pl.len() / p.height).round(3))
    return out, start.join(lg, on="start_type", how="left")


def player_profiles(staged: dict[str, pl.DataFrame], enriched: dict[str, pl.DataFrame],
                    shooting: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """-> (one row per player and team, long percentile table for qualified players)."""
    mp, mt, ev = staged["match_players"], staged["match_teams"], enriched["events_enriched"]
    box = mp.filter(pl.col("played")).group_by("team_season_id", "player_id").agg(
        games=pl.len(), starts=pl.col("starter").sum(), seconds=pl.col("seconds").sum(),
        plus_minus=pl.col("plus_minus").sum(), *[pl.col(c).sum() for c in BOX])

    # on court: possessions and points from stints
    st = enriched["stints"]
    on = st.explode("lineup").rename({"lineup": "player_id"}).group_by("team_season_id", "player_id").agg(
        on_poss_for=pl.col("poss_for").sum(), on_poss_against=pl.col("poss_against").sum(),
        on_pts_for=pl.col("pts_for").sum(), on_pts_against=pl.col("pts_against").sum())
    team_tot = st.group_by("team_season_id").agg(t_poss_for=pl.col("poss_for").sum(), t_poss_against=pl.col("poss_against").sum(),
                                                 t_pts_for=pl.col("pts_for").sum(), t_pts_against=pl.col("pts_against").sum())

    # on court: team and opponent events (field goals, rebounds, 2PA) from the lineups on each event
    home = mt.filter(pl.col("is_home")).select("match_id", home_id="team_season_id")
    away = mt.filter(~pl.col("is_home")).select("match_id", away_id="team_season_id")
    e = ev.filter(pl.col("team_season_id").is_not_null() & (
        pl.col("action_type").is_in(["2pt", "3pt", "freethrow", "turnover"])
        | ((pl.col("action_type") == "rebound") & pl.col("sub_type").is_in(["offensive", "defensive"])))
    ).join(home, on="match_id").join(away, on="match_id")
    sides = pl.concat([
        e.explode("home_lineup").select("action_type", "sub_type", "success", actor="team_season_id",
                                        player_id="home_lineup", team_season_id="home_id"),
        e.explode("away_lineup").select("action_type", "sub_type", "success", actor="team_season_id",
                                        player_id="away_lineup", team_season_id="away_id"),
    ])
    own, a, sub = pl.col("actor") == pl.col("team_season_id"), pl.col("action_type"), pl.col("sub_type")
    oc = sides.group_by("team_season_id", "player_id").agg(
        on_team_fgm=(own & a.is_in(["2pt", "3pt"]) & pl.col("success")).sum(),
        on_team_plays=(own & a.is_in(["2pt", "3pt"])).sum() + 0.44 * (own & (a == "freethrow")).sum()
        + (own & (a == "turnover")).sum(),
        on_team_oreb=(own & (a == "rebound") & (sub == "offensive")).sum(),
        on_team_dreb=(own & (a == "rebound") & (sub == "defensive")).sum(),
        on_opp_oreb=(~own & (a == "rebound") & (sub == "offensive")).sum(),
        on_opp_dreb=(~own & (a == "rebound") & (sub == "defensive")).sum(),
        on_opp_fg2a=(~own & (a == "2pt")).sum())

    names = mp.group_by("player_id").agg(player=pl.col("name").first())
    info = staged["players"].select("player_id", "nationality", "birth_date").unique("player_id").with_columns(
        age=((pl.lit(SEASON_START) - pl.col("birth_date")).dt.total_days() / 365.25).round(1))
    teams = staged["teams"].select("team_season_id", team="name")
    shoot = shooting.select("team_season_id", "player_id", xpts_per_shot="xpts_per_shot", poe_per_shot="poe_per_shot",
                            poe_se="poe_se")
    d = (box.join(on, on=["team_season_id", "player_id"], how="left").join(oc, on=["team_season_id", "player_id"], how="left")
         .join(team_tot, on="team_season_id").join(shoot, on=["team_season_id", "player_id"], how="left")
         .join(names, on="player_id").join(teams, on="team_season_id").join(info.drop("birth_date"), on="player_id", how="left"))
    m = pl.col("seconds") / 60
    d = d.with_columns(
        minutes=m.round(1), mpg=_div(m, pl.col("games"), digits=1),
        ppg=_div(pl.col("pts"), pl.col("games"), digits=1), rpg=_div(pl.col("reb"), pl.col("games"), digits=1),
        apg=_div(pl.col("ast"), pl.col("games"), digits=1),
        pts40=_div(pl.col("pts"), m, 40, 1), reb40=_div(pl.col("reb"), m, 40, 1), ast40=_div(pl.col("ast"), m, 40, 1),
        ts=_div(pl.col("pts"), 2 * (pl.col("fga") + 0.44 * pl.col("fta"))),
        efg=_div(pl.col("fgm") + 0.5 * pl.col("fg3m"), pl.col("fga")),
        fg3a_rate=_div(pl.col("fg3a"), pl.col("fga")), ft_rate=_div(pl.col("fta"), pl.col("fga")),
        fg3_pct=_div(pl.col("fg3m"), pl.col("fg3a")), ft_pct=_div(pl.col("ftm"), pl.col("fta")),
        usg=_div(pl.col("fga") + 0.44 * pl.col("fta") + pl.col("tov"), pl.col("on_team_plays")),
        ast_pct=_div(pl.col("ast"), pl.col("on_team_fgm") - pl.col("fgm")),
        tov_pct=_div(pl.col("tov"), pl.col("fga") + 0.44 * pl.col("fta") + pl.col("tov")),
        orb_pct=_div(pl.col("oreb"), pl.col("on_team_oreb") + pl.col("on_opp_dreb")),
        drb_pct=_div(pl.col("dreb"), pl.col("on_team_dreb") + pl.col("on_opp_oreb")),
        stl_pct=_div(pl.col("stl"), pl.col("on_poss_against")),
        blk_pct=_div(pl.col("blk"), pl.col("on_opp_fg2a")),
        on_ortg=_div(pl.col("on_pts_for"), pl.col("on_poss_for"), 100, 1),
        on_drtg=_div(pl.col("on_pts_against"), pl.col("on_poss_against"), 100, 1),
        off_ortg=_div(pl.col("t_pts_for") - pl.col("on_pts_for"), pl.col("t_poss_for") - pl.col("on_poss_for"), 100, 1),
        off_drtg=_div(pl.col("t_pts_against") - pl.col("on_pts_against"),
                      pl.col("t_poss_against") - pl.col("on_poss_against"), 100, 1),
        qualified=m >= QUALIFY_MIN,
    ).with_columns(on_net=(pl.col("on_ortg") - pl.col("on_drtg")).round(1),
                   off_net=(pl.col("off_ortg") - pl.col("off_drtg")).round(1)).with_columns(
        on_off=(pl.col("on_net") - pl.col("off_net")).round(1)).drop("t_poss_for", "t_poss_against", "t_pts_for",
                                                                    "t_pts_against")

    metrics = {"ppg": "Sayı / maç", "pts40": "Sayı / 40 dk", "ts": "TS%", "efg": "eFG%", "usg": "Kullanım (USG%)",
               "ast_pct": "Asist %", "tov_pct": "Top kaybı %", "orb_pct": "Hücum rib. %", "drb_pct": "Savunma rib. %",
               "stl_pct": "Top çalma %", "blk_pct": "Blok %", "fg3a_rate": "3PA oranı", "ft_rate": "FT oranı",
               "poe_per_shot": "Şut başarısı (POE/şut)", "xpts_per_shot": "Şut kalitesi (xPTS/şut)", "on_off": "On/off net"}
    lower_better = {"tov_pct"}
    q = d.filter(pl.col("qualified"))
    long = []
    for k, label in metrics.items():
        rank = pl.col(k).rank("average") if k not in lower_better else (-pl.col(k)).rank("average")
        long.append(q.select("team_season_id", "player_id", "player", "team", value=pl.col(k).cast(pl.Float64)).with_columns(
            metric=pl.lit(k), label=pl.lit(label),
            pct=((q.select(rank).to_series() - 0.5) / q.select(pl.col(k).is_not_null().sum()).item() * 100).round(0)))
    pct = pl.concat(long).filter(pl.col("value").is_not_null())
    return d, pct
