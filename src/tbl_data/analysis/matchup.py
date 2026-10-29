"""Opponent scouting (Rakip Analizi): how two teams match up, and what the other team does.

Team strength: every team-game's points per possession is fitted as league average + the
team's offence + the opponent's defence ± home court, possession weighted, with a ridge
penalty (each rating shrinks to league average until the games pull it away). In a
walk-forward test on 2025-26 (each game projected from the games before it) this beat raw
ratings and home court alone: mean absolute error 9.2 points, winner right 70%, Brier 0.199
vs 0.230 for home court only; penalties 300-1000 did best, 10,000 shrank too far.

A matchup is projected multiplicatively (Pomeroy / Basketball-Reference): expected pace =
pace_A * pace_B / league pace; each side's points per possession from the ratings; win
probability = Phi(margin / sigma), sigma = the walk-forward error SD (11.7 in 2025-26).

Four factors are matched the same way: expected = attacker's value * defender's allowed
value / league. A factor's weight (points per 100 possessions per point of the factor) comes
from a regression of team-game ORtg on the four factors.

Team-stat reliability: one-way intraclass correlation of per-game values across teams;
reliability after n games = n*ICC / (1 + (n-1)*ICC). At 8 games style stats settle (net,
transition share, 3PA rate, pace ~0.5-0.6), defensive results do not (opponent 3P% 0.03).
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl

RIDGE_LAMBDA = 1000.0
MIN_TEAM_GAMES = 3  # a game is projected in the backtest once both teams have played this many
SIGMA_DEFAULT = 12.0  # margin SD when no season is long enough to measure it
SIGMA_MIN_GAMES = 100  # backtest games needed to trust a season's own SD
Z80 = 1.2816
VENUES = {"Ev": 1, "Deplasman": -1, "Tarafsız": 0}
AUTO = "Fikstüre göre"
FACTORS = ["efg", "tov", "orb", "ftr"]
# fallback four-factor weights (2025-26 + 2026-27 team-games, R² 0.945)
WEIGHTS_DEFAULT = {"efg": 1.46, "tov": -1.24, "orb": 0.61, "ftr": 0.25}
WEIGHTS_MIN_GAMES = 60  # team-games needed to fit the weights on this season
RELIABILITY_MIN_GAMES = 16  # median team games before this season's ICC replaces last season's
TIERS = ((0.45, "oturdu"), (0.25, "kısmen"), (-1.0, "gürültü"))
c = pl.col


def tier(rel: float | None) -> str | None:
    if rel is None or (isinstance(rel, float) and math.isnan(rel)):
        return None
    return next(name for lo, name in TIERS if rel >= lo)


# -- team-games -------------------------------------------------------------------------
BOX = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "tov"]


def team_games(staged: dict[str, pl.DataFrame], enriched: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """One row per played team-game (no forfeits): box score, counted possessions, the opponent's."""
    m = staged["matches"].filter(~c("forfeit")).select("match_id", "start_utc", "week", "n_overtimes")
    poss = enriched["possessions"].group_by("match_id", "team_season_id").agg(poss=pl.len())
    g = staged["match_teams"].select("match_id", "team_season_id", "is_home", *BOX).join(
        poss, on=["match_id", "team_season_id"]).join(m, on="match_id")
    o = g.select("match_id", opp_id="team_season_id", **{f"o_{k}": k for k in [*BOX, "poss"]})
    return g.join(o, on="match_id").filter(c("team_season_id") != c("opp_id")).with_columns(
        pace=(c("poss") + c("o_poss")) / 2 * 2400 / (2400 + 300 * c("n_overtimes"))).sort("start_utc", "match_id")


# -- ratings ----------------------------------------------------------------------------
def fit_ratings(g: pl.DataFrame, lam: float = RIDGE_LAMBDA) -> tuple[float, dict, dict, float]:
    """team-games -> (league ppp, offence, defence, home): ppp = mu + off[team] + def[opp] ± home."""
    teams = sorted(set(g["team_season_id"]) | set(g["opp_id"]))
    ix = {t: i for i, t in enumerate(teams)}
    n, rows = len(teams), g.height
    X = np.zeros((rows, 2 * n + 1))
    r = np.arange(rows)
    X[r, [ix[t] for t in g["team_season_id"]]] = 1
    X[r, [n + ix[t] for t in g["opp_id"]]] = 1
    X[:, -1] = np.where(g["is_home"].to_numpy(), 1.0, -1.0)
    w = g["poss"].to_numpy().astype(float)
    y = (g["pts"] / g["poss"]).to_numpy()
    mu = float(np.average(y, weights=w))
    P = lam * np.eye(2 * n + 1)
    P[-1, -1] = 1e-6  # home court is not shrunk
    b = np.linalg.solve(X.T @ (X * w[:, None]) + P, X.T @ (w * (y - mu)))
    return mu, {t: b[ix[t]] for t in teams}, {t: b[n + ix[t]] for t in teams}, float(b[-1])


def project(mu: float, off: dict, de: dict, home: float, pace: dict, lg_pace: float, a: int, b: int, side: int
            ) -> tuple[float, float, float]:
    """-> (pace, A's points per possession, B's) with side +1 A at home, -1 away, 0 neutral."""
    p = pace[a] * pace[b] / lg_pace
    return p, mu + off[a] + de[b] + side * home, mu + off[b] + de[a] - side * home


def backtest(g: pl.DataFrame) -> pl.DataFrame:
    """Walk-forward: each game projected from the games before its start time."""
    rows = []
    home_games = g.filter(c("is_home"))
    for start, day in home_games.group_by("start_utc", maintain_order=True):
        past = g.filter(c("start_utc") < start[0])
        if past.is_empty():
            continue
        n = dict(past.group_by("team_season_id").len().iter_rows())
        todo = day.filter(c("team_season_id").replace_strict(n, default=0).ge(MIN_TEAM_GAMES)
                          & c("opp_id").replace_strict(n, default=0).ge(MIN_TEAM_GAMES))
        if todo.is_empty():
            continue
        mu, off, de, h = fit_ratings(past)
        pace = dict(past.group_by("team_season_id").agg(c("pace").mean()).iter_rows())
        lg = float(past["pace"].mean())
        for r in todo.iter_rows(named=True):
            p, ea, eb = project(mu, off, de, h, pace, lg, r["team_season_id"], r["opp_id"], 1)
            rows.append({"match_id": r["match_id"], "start_utc": r["start_utc"], "home_id": r["team_season_id"],
                         "away_id": r["opp_id"], "n_min": min(n[r["team_season_id"]], n[r["opp_id"]]),
                         "predicted": round((ea - eb) * p, 2), "actual": r["pts"] - r["o_pts"]})
    schema = {"match_id": pl.Int32, "start_utc": g.schema["start_utc"], "home_id": pl.Int32, "away_id": pl.Int32,
              "n_min": pl.Int64, "predicted": pl.Float64, "actual": pl.Int32}
    return pl.DataFrame(rows, schema=schema)


def backtest_scores(bt: pl.DataFrame, sigma: float) -> dict:
    from scipy.stats import norm

    if bt.is_empty():
        return {"games": 0}
    y = (bt["actual"] > 0).to_numpy()
    pred, act = bt["predicted"].to_numpy(), bt["actual"].to_numpy()
    p = norm.cdf(pred / sigma)
    base = float(y.mean())  # home court alone: the home win rate
    return {"games": bt.height, "mae": round(float(np.abs(act - pred).mean()), 2),
            "accuracy": round(float(((pred > 0) == y).mean()), 3), "brier": round(float(((p - y) ** 2).mean()), 3),
            "brier_home": round(float(((base - y) ** 2).mean()), 3), "home_win": round(base, 3),
            "sd": round(float((act - pred).std()), 2)}


# -- four factors -----------------------------------------------------------------------
def _factor_values(df: pl.DataFrame) -> pl.DataFrame:
    """summed counts -> offence (own) and defence (allowed) factor values."""
    return df.with_columns(
        efg=(c("fgm") + 0.5 * c("fg3m")) / c("fga"), tov=c("tov") / c("poss"), orb=c("oreb") / (c("oreb") + c("o_dreb")),
        ftr=c("ftm") / c("fga"), r3=c("fg3a") / c("fga"),
        d_efg=(c("o_fgm") + 0.5 * c("o_fg3m")) / c("o_fga"), d_tov=c("o_tov") / c("o_poss"),
        d_orb=c("o_oreb") / (c("o_oreb") + c("dreb")), d_ftr=c("o_ftm") / c("o_fga"), d_r3=c("o_fg3a") / c("o_fga"))


def factor_weights(g: pl.DataFrame) -> tuple[dict[str, float], str]:
    """ORtg points per point (pp) of each factor, from team-games; fallback constants for short seasons."""
    if g.height < WEIGHTS_MIN_GAMES:
        return WEIGHTS_DEFAULT, "default"
    f = _factor_values(g).with_columns(ortg=100 * c("pts") / c("poss"))
    X = np.column_stack([np.ones(f.height)] + [f[k].to_numpy() for k in FACTORS])
    b = np.linalg.lstsq(X, f["ortg"].to_numpy(), rcond=None)[0]
    return {k: round(float(v) / 100, 3) for k, v in zip(FACTORS, b[1:])}, "season"


def factor_table(g: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    sums = [c(k).sum() for k in [*BOX, "poss", *[f"o_{k}" for k in [*BOX, "poss"]]]]
    t = _factor_values(g.group_by("team_season_id").agg(sums))
    lg = _factor_values(g.select(sums)).row(0, named=True)
    return t, {k: lg[k] for k in [*FACTORS, "r3"]}


# a higher offence value is better except turnovers; a lower allowed value is better except turnovers forced
OFF_DESC = {"efg": True, "tov": False, "orb": True, "ftr": True, "r3": True}


def matchup_factors(t: pl.DataFrame, lg: dict, w: dict) -> pl.DataFrame:
    ranks = t.select("team_season_id", *[c(k).rank("min", descending=OFF_DESC[k]).alias(f"rk_{k}") for k in OFF_DESC],
                     *[c(f"d_{k}").rank("min", descending=not OFF_DESC[k]).alias(f"rk_d_{k}") for k in OFF_DESC])
    v = {r["team_season_id"]: r for r in t.join(ranks, on="team_season_id").iter_rows(named=True)}
    rows = []
    for a in v:
        for b in v:
            if a == b:
                continue
            for direction, att, dfn in (("offense", a, b), ("defense", b, a)):
                for k in OFF_DESC:
                    e = v[att][k] * v[dfn][f"d_{k}"] / lg[k] if lg[k] else None
                    gain = w[k] * (e - lg[k]) * 100 if k in w and e is not None else None
                    rows.append({"team_season_id": a, "opp_id": b, "direction": direction, "factor": k,
                                 "att_value": v[att][k], "att_rank": v[att][f"rk_{k}"], "def_value": v[dfn][f"d_{k}"],
                                 "def_rank": v[dfn][f"rk_d_{k}"], "league_value": lg[k], "expected": e,
                                 # points per 100 possessions for the attacking side, then from team A's side
                                 "impact": None if gain is None else (gain if direction == "offense" else -gain)})
    return pl.DataFrame(rows)


# -- shots by zone ------------------------------------------------------------------------
ZONES = ["rim", "paint", "midrange", "corner_3", "above_break_3"]


def zone_clash(shots: pl.DataFrame, g: pl.DataFrame) -> pl.DataFrame:
    """team x side x zone: share of attempts, FG%, xPTS per shot; offence = own shots, defence = allowed."""
    s = shots.filter(c("zone").is_in(ZONES))
    opp = g.select("match_id", "team_season_id", "opp_id")
    agg = [pl.len().alias("shots"), c("made").mean().alias("fg"), c("xpts").mean().alias("xpts_per_shot")]
    off = s.group_by("team_season_id", "zone").agg(agg).with_columns(side=pl.lit("offense"))
    d = s.join(opp, on=["match_id", "team_season_id"]).group_by(team_season_id="opp_id", zone="zone").agg(agg).with_columns(
        side=pl.lit("defense"))
    lg = s.group_by("zone").agg(agg).with_columns(side=pl.lit("league"), team_season_id=pl.lit(None, pl.Int32))
    out = pl.concat([off, d, lg.select(off.columns)])
    return out.with_columns(share=c("shots") / c("shots").sum().over("team_season_id", "side"))


# -- team-stat reliability ----------------------------------------------------------------
def _per_game(g: pl.DataFrame, shots: pl.DataFrame, periods: pl.DataFrame) -> pl.DataFrame:
    keys = ["match_id", "team_season_id"]
    z = shots.group_by(keys).agg(rim=(c("zone") == "rim").mean(), mid=(c("zone") == "midrange").mean(),
                                 trans=c("transition").cast(pl.Float64).mean(), xps=c("xpts").mean(),
                                 fg3=c("made").filter(c("shot_type") == "3pt").mean())
    q1 = periods.filter(c("period_ord") == 1).select(*keys, q1="pts")
    d = g.join(z, on=keys, how="left").join(q1, on=keys, how="left")
    o = d.select("match_id", opp_id="team_season_id", o_rim="rim", o_trans="trans", o_xps="xps", o_fg3="fg3", o_q1="q1")
    return _factor_values(d.join(o, on=["match_id", "opp_id"])).with_columns(
        ortg=100 * c("pts") / c("poss"), drtg=100 * c("o_pts") / c("o_poss"),
        net=100 * (c("pts") / c("poss") - c("o_pts") / c("o_poss")), q1net=c("q1") - c("o_q1"),
        drb=c("dreb") / (c("dreb") + c("o_oreb")))


# stat key -> label; the key is what team_identity and the page refer to
RELIABILITY_STATS = {
    "net": "Net reyting", "ortg": "ORtg", "drtg": "DRtg", "pace": "Tempo", "efg": "eFG%", "d_efg": "Rakip eFG%",
    "tov": "TOV%", "d_tov": "Rakip TOV%", "orb": "ORB%", "drb": "DRB%", "ftr": "FT oranı", "d_ftr": "Rakip FT oranı",
    "r3": "3PA oranı", "d_r3": "Rakip 3PA oranı", "rim": "Rim payı", "o_rim": "Rakip rim payı", "mid": "Orta mesafe payı",
    "trans": "Geçiş payı", "o_trans": "Rakip geçiş payı", "xps": "xPTS/şut", "o_xps": "Rakip xPTS/şut",
    "fg3": "3P%", "o_fg3": "Rakip 3P%", "q1net": "1. çeyrek farkı",
}


def team_reliability(per_game: pl.DataFrame, stats: dict[str, str] = RELIABILITY_STATS) -> pl.DataFrame:
    """ICC of each per-game team stat: the share of game-to-game spread that is between teams."""
    rows = []
    for k, label in stats.items():
        d = per_game.select("team_season_id", v=c(k)).drop_nulls().filter(c("v").is_finite())
        grp = d.group_by("team_season_id").agg(m=c("v").mean(), s2=c("v").var(), n=pl.len()).filter(c("n") >= 2)
        if grp.height < 3:
            icc = None
        else:
            sw = float((grp["s2"] * (grp["n"] - 1)).sum() / (grp["n"] - 1).sum())
            sb = float(grp["m"].var()) - sw / float(grp["n"].mean())
            icc = max(sb, 0.0) / (max(sb, 0.0) + sw) if sw > 0 else None
        rows.append({"stat": k, "label": label, "icc": icc,
                     "games_for_half": (1 - icc) / icc if icc else None})
    return pl.DataFrame(rows, schema={"stat": pl.String, "label": pl.String, "icc": pl.Float64,
                                      "games_for_half": pl.Float64})


def with_tiers(rel: pl.DataFrame, n_games: float) -> pl.DataFrame:
    return rel.with_columns(rel_now=(n_games * c("icc") / (1 + (n_games - 1) * c("icc"))), games_now=pl.lit(n_games)
                            ).with_columns(tier=c("rel_now").map_elements(tier, return_dtype=pl.String))


# -- rotation, lineups, periods, form, history -------------------------------------------
PERIODS = {1: "Ç1", 2: "Ç2", 3: "Ç3", 4: "Ç4"}
CLOSE_FROM = 2100.0  # the last 5 minutes of the 4th quarter (elapsed seconds)


def rotation(stints: pl.DataFrame, match_players: pl.DataFrame, g: pl.DataFrame) -> pl.DataFrame:
    """team x player: minutes per team game by quarter (overtime together) and in the last 5 minutes of Q4."""
    games = g.group_by("team_season_id").agg(team_games=pl.len())
    st = stints.explode("lineup").rename({"lineup": "player_id"}).with_columns(
        period=c("period_ord").replace_strict(PERIODS, default="Uzatma", return_dtype=pl.String),
        close=pl.when(c("period_ord") == 4).then(
            (pl.min_horizontal(c("end_s"), pl.lit(2400.0)) - pl.max_horizontal(c("start_s"), pl.lit(CLOSE_FROM))).clip(0))
        .otherwise(0.0))
    by_q = st.group_by("team_season_id", "player_id", "period").agg(s=c("seconds").sum())
    close = st.group_by("team_season_id", "player_id").agg(s=c("close").sum()).with_columns(period=pl.lit("Son 5 dk"))
    box = match_players.filter(c("played")).group_by("team_season_id", "player_id").agg(
        games=pl.len(), starts=c("starter").sum(), seconds=c("seconds").sum())
    return (pl.concat([by_q, close.select(by_q.columns)]).join(games, on="team_season_id")
            .with_columns(mpg=c("s") / 60 / c("team_games")).drop("s")
            .join(box, on=["team_season_id", "player_id"], how="left"))


def top_lineups(stints: pl.DataFrame, n: int = 5) -> pl.DataFrame:
    s = stints.with_columns(key=c("lineup").cast(pl.List(pl.String)).list.join(","))
    t = s.group_by("team_season_id", "key").agg(
        lineup=c("lineup").first(), games=c("match_id").n_unique(), seconds=c("seconds").sum(),
        pts_for=c("pts_for").sum(), pts_against=c("pts_against").sum(), poss_for=c("poss_for").sum(),
        poss_against=c("poss_against").sum())
    return t.with_columns(
        net=pl.when((c("poss_for") > 0) & (c("poss_against") > 0)).then(
            100 * (c("pts_for") / c("poss_for") - c("pts_against") / c("poss_against"))),
        rank=c("seconds").rank("ordinal", descending=True).over("team_season_id")).filter(c("rank") <= n).drop("key")


def period_profile(periods: pl.DataFrame, g: pl.DataFrame) -> pl.DataFrame:
    """team x period: points scored and allowed per team game (overtime per game played, all games)."""
    keys = ["match_id", "team_season_id"]
    p = periods.with_columns(period=c("period_ord").replace_strict(PERIODS, default="Uzatma", return_dtype=pl.String))
    p = p.join(g.select(*keys, "opp_id"), on=keys)
    o = p.select("match_id", "period_ord", opp_id="team_season_id", opp_pts="pts")
    p = p.join(o, on=["match_id", "period_ord", "opp_id"])
    games = g.group_by("team_season_id").agg(team_games=pl.len())
    return p.group_by("team_season_id", "period").agg(pts=c("pts").sum(), opp_pts=c("opp_pts").sum()).join(
        games, on="team_season_id").with_columns(pts_pg=c("pts") / c("team_games"), opp_pts_pg=c("opp_pts") / c("team_games"))


def form(g: pl.DataFrame, mu: float, off: dict, de: dict, home: float, pace: dict, lg_pace: float) -> pl.DataFrame:
    """Each team-game: actual margin minus the margin the season's ratings expect (opponent and court)."""
    rows = []
    for r in g.iter_rows(named=True):
        t, o = r["team_season_id"], r["opp_id"]
        p, ea, eb = project(mu, off, de, home, pace, lg_pace, t, o, 1 if r["is_home"] else -1)
        exp = (ea - eb) * p
        rows.append({"match_id": r["match_id"], "team_season_id": t, "opp_id": o, "is_home": r["is_home"],
                     "start_utc": r["start_utc"], "week": r["week"], "pts": r["pts"], "opp_pts": r["o_pts"],
                     "expected": round(exp, 1), "residual": round(r["pts"] - r["o_pts"] - exp, 1)})
    f = pl.DataFrame(rows)
    return f.with_columns(game_no=c("start_utc").rank("ordinal").over("team_season_id"),
                          from_last=c("start_utc").rank("ordinal", descending=True).over("team_season_id"))


def head_to_head(seasons: list[tuple[str, dict[str, pl.DataFrame]]], current_teams: pl.DataFrame) -> pl.DataFrame:
    """Meetings of two clubs in every season given, keyed by this season's team ids (club_id joins seasons)."""
    now = dict(current_teams.select("club_id", "team_season_id").iter_rows())
    rows = []
    for key, st in seasons:
        club = dict(st["teams"].select("team_season_id", "club_id").iter_rows())
        for m in st["matches"].iter_rows(named=True):
            h, a = now.get(club.get(m["home_team_id"])), now.get(club.get(m["away_team_id"]))
            if h is None or a is None:
                continue
            for t, o, home, pts, opp in ((h, a, True, m["home_score"], m["away_score"]),
                                          (a, h, False, m["away_score"], m["home_score"])):
                rows.append({"team_season_id": t, "opp_id": o, "season_key": key, "match_id": m["match_id"],
                             "week": m["week"], "phase": m["phase"], "start_utc": m["start_utc"], "is_home": home,
                             "pts": pts, "opp_pts": opp, "forfeit": m["forfeit"]})
    schema = {"team_season_id": pl.Int32, "opp_id": pl.Int32, "season_key": pl.String, "match_id": pl.Int32,
              "week": pl.Int32, "phase": pl.String, "start_utc": pl.Datetime("us", "UTC"), "is_home": pl.Boolean,
              "pts": pl.Int32, "opp_pts": pl.Int32, "forfeit": pl.Boolean}
    return pl.DataFrame(rows, schema=schema)


# -- everything -------------------------------------------------------------------------------
IDENTITY = [  # (key, label, higher is better: True/False/None for style), reliability stat
    ("adj_net", "Düzeltilmiş net", True, "net"), ("adj_o", "Düzeltilmiş ORtg", True, "ortg"),
    ("adj_d", "Düzeltilmiş DRtg", False, "drtg"), ("pace", "Tempo", None, "pace"), ("r3", "3PA oranı", None, "r3"),
    ("trans", "Geçiş payı", None, "trans"), ("rim", "Rim payı", None, "rim"), ("mid", "Orta mesafe payı", None, "mid"),
    ("ftr", "FT oranı", True, "ftr"), ("xps", "xPTS/şut (hücum)", True, "xps"),
    ("o_xps", "xPTS/şut (verdiği)", False, "o_xps"), ("q1net", "1. çeyrek farkı/maç", True, "q1net"),
]


def analyze_matchups(staged: dict[str, pl.DataFrame], enriched: dict[str, pl.DataFrame], shots: pl.DataFrame,
                     clutch_team: pl.DataFrame, prior_tables: dict[str, pl.DataFrame] | None = None,
                     prior_staged: dict[str, pl.DataFrame] | None = None, prior_season: str | None = None,
                     season: str = "", fixtures: pl.DataFrame | None = None) -> tuple[dict[str, pl.DataFrame], dict]:
    """-> (tables, metrics). shots: shot_xpts features (zone, transition, xpts). fixtures: upcoming games
    (match_id, week, start_utc, home_team_id, away_team_id) to resolve the 'Fikstüre göre' court."""
    pt = prior_tables or {}
    g = team_games(staged, enriched)
    mu, off, de, home = fit_ratings(g)
    pace = dict(g.group_by("team_season_id").agg(c("pace").mean()).iter_rows())
    lg_pace = float(g["pace"].mean())

    bt = backtest(g)
    prior_bt = pt.get("matchup_backtest")
    if prior_bt is not None and prior_bt.height >= SIGMA_MIN_GAMES:
        sigma, sigma_from = float((prior_bt["actual"] - prior_bt["predicted"]).std()), prior_season
    elif bt.height >= SIGMA_MIN_GAMES:
        sigma, sigma_from = float((bt["actual"] - bt["predicted"]).std()), season
    else:
        sigma, sigma_from = SIGMA_DEFAULT, "default"
    scores = backtest_scores(bt, sigma)
    prior_scores = backtest_scores(prior_bt, sigma) if prior_bt is not None and prior_bt.height else {"games": 0}

    raw = g.group_by("team_season_id").agg(
        games=pl.len(), wins=(c("pts") > c("o_pts")).sum(), ortg=100 * c("pts").sum() / c("poss").sum(),
        drtg=100 * c("o_pts").sum() / c("o_poss").sum())
    ratings = raw.with_columns(
        adj_o=100 * (mu + c("team_season_id").replace_strict(off, return_dtype=pl.Float64)),
        adj_d=100 * (mu + c("team_season_id").replace_strict(de, return_dtype=pl.Float64)),
        pace=c("team_season_id").replace_strict(pace, return_dtype=pl.Float64)).with_columns(
        net=c("ortg") - c("drtg"), adj_net=c("adj_o") - c("adj_d"))

    # every ordered pair on every court
    nxt: dict[tuple[int, int], dict] = {}
    if fixtures is not None:
        for f in fixtures.sort("start_utc").iter_rows(named=True):
            for t, o, side in ((f["home_team_id"], f["away_team_id"], "Ev"), (f["away_team_id"], f["home_team_id"], "Deplasman")):
                nxt.setdefault((t, o), {"venue": side, "week": f["week"], "start_utc": f["start_utc"], "match_id": f["match_id"]})
    teams = sorted(off)
    pairs = []
    for a in teams:
        for b in teams:
            if a == b:
                continue
            fx = nxt.get((a, b))
            for venue in [*VENUES, AUTO]:
                v = (fx["venue"] if fx else "Ev") if venue == AUTO else venue
                p, ea, eb = project(mu, off, de, home, pace, lg_pace, a, b, VENUES[v])
                m = (ea - eb) * p
                pairs.append({"team_season_id": a, "opp_id": b, "venue": venue, "court": v, "pace": p,
                              "ortg": 100 * ea, "opp_ortg": 100 * eb, "pts": ea * p, "opp_pts": eb * p, "margin": m,
                              "lo80": m - Z80 * sigma, "hi80": m + Z80 * sigma,
                              "p_win": 0.5 * (1 + math.erf(m / sigma / math.sqrt(2))),
                              "fixture_match_id": fx["match_id"] if fx else None,
                              "fixture_week": fx["week"] if fx else None,
                              "fixture_start": fx["start_utc"] if fx else None})
    pair_schema = {"fixture_match_id": pl.Int32, "fixture_week": pl.Int32,
                   "fixture_start": fixtures.schema["start_utc"] if fixtures is not None else pl.Datetime("us", "UTC")}
    pairs_df = pl.DataFrame(pairs, schema_overrides=pair_schema)

    w, w_from = factor_weights(g)
    ft, lg = factor_table(g)
    factors = matchup_factors(ft, lg, w)
    zones = zone_clash(shots, g)

    per_game = _per_game(g, shots, staged["period_scores"])
    n_now = float(g.group_by("team_season_id").len()["len"].median())
    if n_now >= RELIABILITY_MIN_GAMES or "team_reliability" not in pt:
        rel = team_reliability(per_game).with_columns(fitted_on=pl.lit(season))
    else:
        rel = pt["team_reliability"].select("stat", "label", "icc", "games_for_half", "fitted_on")
    rel = with_tiers(rel, n_now)

    # identity: season values per team with league rank
    pg = per_game.group_by("team_season_id").agg(
        *[c(k).mean() for k in ("rim", "mid", "trans", "xps", "o_xps")], q1net=c("q1net").mean())
    ident = ratings.join(ft.select("team_season_id", "r3", "ftr"), on="team_season_id").join(pg, on="team_season_id")
    rel_of = {r["stat"]: r for r in rel.iter_rows(named=True)}
    id_rows = []
    for key, label, better, stat in IDENTITY:
        ranked = ident.with_columns(rank=c(key).rank("min", descending=better is not False))
        for r in ranked.iter_rows(named=True):
            id_rows.append({"team_season_id": r["team_season_id"], "metric": key, "label": label, "value": r[key],
                            "rank": r["rank"] if better is not None else None, "style": better is None,
                            "tier": rel_of.get(stat, {}).get("tier")})
    cl = clutch_team.select("team_season_id", "poss", "net")
    identity = pl.DataFrame(id_rows).join(cl.rename({"poss": "clutch_poss", "net": "clutch_net"}), on="team_season_id",
                                          how="left")

    seasons = [(season, staged)] + ([(prior_season, prior_staged)] if prior_staged else [])
    tables = {
        "team_ratings": ratings,
        "matchup_backtest": bt,
        "matchup_pairs": pairs_df,
        "matchup_factors": factors,
        "matchup_zones": zones,
        "team_reliability": rel,
        "team_identity": identity,
        "rotation": rotation(enriched["stints"], staged["match_players"], g),
        "top_lineups": top_lineups(enriched["stints"]),
        "period_profile": period_profile(staged["period_scores"], g),
        "team_form": form(g, mu, off, de, home, pace, lg_pace),
        "head_to_head": head_to_head(seasons, staged["teams"]),
    }
    metrics = {"lambda": RIDGE_LAMBDA, "league_ppp": round(mu, 4), "league_pace": round(lg_pace, 2),
               "home_ppp": round(home, 4), "home_pts": round(2 * home * lg_pace, 2), "sigma": round(sigma, 2),
               "sigma_from": sigma_from, "backtest": scores, "prior_backtest": prior_scores,
               "prior_season": prior_season, "weights": w, "weights_from": w_from,
               "reliability_from": rel["fitted_on"][0] if rel.height else None, "games_now": n_now}
    return tables, metrics
