"""How much of a player's number is skill and how much is luck, and how to lean on last season.

Every player rate is a mean over some unit (FT% over free throws, USG% over the team's plays
on court, POE over shots). Measured over n units, it has a sampling variance sigma^2 / n
around the player's true rate, and true rates spread across players with variance tau^2.

  sigma^2: from split halves. Odd and even games of the same player measure the same true
           rate, so E[(p_odd - p_even)^2] = sigma^2 (1/n_odd + 1/n_even).
  tau^2:   method of moments on the full season: the n-weighted spread of player rates
           around the league mean, minus what sampling alone would give.
  k = sigma^2 / tau^2 is the stabilization point: after k units a rate is half skill, half
  noise (reliability n / (n + k)), and the best estimate pads it with k units of league
  average: (n p + k mu) / (n + k)  (Kostya Medvedovsky's padding; empirical Bayes).

With last season as well, its units count at a discount d (a player changes from year to
year), tuned by a backtest: predict the rest of a season from its first games and the
season before (TBF's career totals, box score only):
  estimate = (n p + d n_prev p_prev + k mu) / (n + d n_prev + k),  SE = sigma / sqrt(n + d n_prev + k)
d is shared within a group of rates, and capped at 1 (a unit last season is worth at most a
unit this season): shooting (FT%, 3P%, 2P%, eFG%, TS%; POE), role (3PA and FT rate, TOV%;
shot quality) and production (per minute of play: plays, assists, rebounds, steals, blocks;
USG%, AST%, rebound, steal and block % take it from there, as they need the lineups).

On/off is the team's net rating with the player on minus off; its unit is the effective
number of possessions 1 / (1/on + 1/off). It gets no prior: it depends on the teammates.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

SENIOR = ("TBL", "BSL", "TB2L", "EBBL", "BGL")  # league competitions in TBF's career table (no cups, youth)
MIN_UNITS = 5  # per half for sigma^2, per season for tau^2
BOOT = 400
D_GRID = np.round(np.arange(0, 1.01, 0.05), 2)
GROUPS = {"isabet": "İsabet", "rol": "Rol", "uretim": "Üretim"}
BACKTEST_GAMES = (2, 4, 8)
BACKTEST_MIN_REST = 10  # units in the rest of the season
SEED = 7


@dataclass(frozen=True)
class Stat:
    name: str
    label: str
    num: pl.Expr
    den: pl.Expr
    box: bool  # computable from TBF's box-score career totals
    group: str  # shares one prior-season weight d: shooting, role, production
    shown: bool = True  # a dashboard rate (False: a per-minute stand-in, only here to tune d)


c = pl.col
PLAYS = c("fga") + 0.44 * c("fta") + c("tov")
STATS = [
    Stat("ft_pct", "FT%", c("ftm"), c("fta"), True, "isabet"),
    Stat("fg3_pct", "3P%", c("fg3m"), c("fg3a"), True, "isabet"),
    Stat("fg2_pct", "2P%", c("fg2m"), c("fg2a"), True, "isabet"),
    Stat("efg", "eFG%", c("fgm") + 0.5 * c("fg3m"), c("fga"), True, "isabet"),
    Stat("ts", "TS%", c("pts") / 2, c("fga") + 0.44 * c("fta"), True, "isabet"),
    Stat("fg3a_rate", "3PA oranı", c("fg3a"), c("fga"), True, "rol"),
    Stat("ft_rate", "FT oranı", c("fta"), c("fga"), True, "rol"),
    Stat("tov_pct", "Top kaybı %", c("tov"), PLAYS, True, "rol"),
    Stat("usg", "Kullanım (USG%)", PLAYS, c("on_team_plays"), False, "uretim"),
    Stat("ast_pct", "Asist %", c("ast"), c("on_team_fgm") - c("fgm"), False, "uretim"),
    Stat("orb_pct", "Hücum rib. %", c("oreb"), c("on_team_oreb") + c("on_opp_dreb"), False, "uretim"),
    Stat("drb_pct", "Savunma rib. %", c("dreb"), c("on_team_dreb") + c("on_opp_oreb"), False, "uretim"),
    Stat("stl_pct", "Top çalma %", c("stl"), c("on_poss_against"), False, "uretim"),
    Stat("blk_pct", "Blok %", c("blk"), c("on_opp_fg2a"), False, "uretim"),
    Stat("xpts_per_shot", "Şut kalitesi (xPTS/şut)", c("xpts"), c("shots"), False, "rol"),
    Stat("poe_per_shot", "Şut başarısı (POE/şut)", c("shot_pts") - c("xpts"), c("shots"), False, "isabet"),
    # per minute of play: box-score stand-ins for the lineup rates
    Stat("plays_pm", "Hücum kullanımı / dk", PLAYS, c("seconds") / 60, True, "uretim", shown=False),
    Stat("ast_pm", "Asist / dk", c("ast"), c("seconds") / 60, True, "uretim", shown=False),
    Stat("oreb_pm", "Hücum rib. / dk", c("oreb"), c("seconds") / 60, True, "uretim", shown=False),
    Stat("dreb_pm", "Savunma rib. / dk", c("dreb"), c("seconds") / 60, True, "uretim", shown=False),
    Stat("stl_pm", "Top çalma / dk", c("stl"), c("seconds") / 60, True, "uretim", shown=False),
    Stat("blk_pm", "Blok / dk", c("blk"), c("seconds") / 60, True, "uretim", shown=False),
]
ON_OFF = "on_off"
UNBOUNDED = {"xpts_per_shot", "poe_per_shot", "ft_rate", "efg", "ts", "plays_pm", "ast_pm", "oreb_pm", "dreb_pm", "stl_pm", "blk_pm",
             ON_OFF}
UNIT = {"ft_pct": "serbest atış", "fg3_pct": "üçlük", "fg2_pct": "ikilik", "efg": "şut", "ts": "şut (TS)",
        "fg3a_rate": "şut", "ft_rate": "şut", "tov_pct": "hücum kullanımı", "usg": "takım hücumu",
        "ast_pct": "takım arkadaşı basketi", "orb_pct": "hücum rib. fırsatı", "drb_pct": "savunma rib. fırsatı",
        "stl_pct": "rakip hücum", "blk_pct": "rakip ikilik", "xpts_per_shot": "şut", "poe_per_shot": "şut",
        "plays_pm": "dakika", "ast_pm": "dakika", "oreb_pm": "dakika", "dreb_pm": "dakika", "stl_pm": "dakika",
        "blk_pm": "dakika", ON_OFF: "hücum (etkin)"}
COUNTS = ["pts", "fgm", "fga", "fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "tov", "stl", "blk",
          "seconds", "games", "on_team_plays", "on_team_fgm", "on_team_oreb", "on_team_dreb", "on_opp_oreb",
          "on_opp_dreb", "on_poss_for", "on_poss_against", "on_pts_for", "on_pts_against", "on_opp_fg2a", "shots",
          "shot_pts", "xpts", "t_poss_for", "t_poss_against", "t_pts_for", "t_pts_against"]


def _units(df: pl.DataFrame) -> pl.DataFrame:
    """Summed counts -> (num, den) per stat, plus on/off as a value with its effective units.
    Box-score counts only (TBF career totals): the box-score stats."""
    if "on_poss_for" not in df.columns:
        return df.with_columns(*[s.num.cast(pl.Float64).alias(f"{s.name}__num") for s in STATS if s.box],
                               *[s.den.cast(pl.Float64).alias(f"{s.name}__den") for s in STATS if s.box])
    on = (c("on_poss_for") + c("on_poss_against")) / 2
    off = (c("t_poss_for") - c("on_poss_for") + c("t_poss_against") - c("on_poss_against")) / 2
    net = lambda pf, pa, nf, na: 100 * (pf / nf - pa / na)  # noqa: E731
    on_off = (net(c("on_pts_for"), c("on_pts_against"), c("on_poss_for"), c("on_poss_against"))
              - net(c("t_pts_for") - c("on_pts_for"), c("t_pts_against") - c("on_pts_against"),
                    c("t_poss_for") - c("on_poss_for"), c("t_poss_against") - c("on_poss_against")))
    ok = (c("on_poss_for") > 0) & (c("on_poss_against") > 0) & (off > 0)
    return df.with_columns(
        *[s.num.cast(pl.Float64).alias(f"{s.name}__num") for s in STATS],
        *[s.den.cast(pl.Float64).alias(f"{s.name}__den") for s in STATS],
        pl.when(ok).then(on_off).alias(f"{ON_OFF}__val"),
        pl.when(ok).then(1 / (1 / on + 1 / off)).otherwise(0.0).alias(f"{ON_OFF}__den"))


def _pn(df: pl.DataFrame, name: str) -> tuple[np.ndarray, np.ndarray]:
    """-> (rate, units), units 0 where the rate is undefined."""
    n = df[f"{name}__den"].fill_null(0).to_numpy().astype(float)
    if name == ON_OFF:
        p = df[f"{name}__val"].fill_null(0).to_numpy().astype(float)
    else:
        num = df[f"{name}__num"].fill_null(0).to_numpy().astype(float)
        p = np.divide(num, n, out=np.zeros_like(n), where=n > 0)
    n = np.where(np.isfinite(p) & (n > 0), n, 0.0)
    return np.where(n > 0, p, 0.0), n


def _available(df: pl.DataFrame) -> list[str]:
    return [s for s in [*(x.name for x in STATS), ON_OFF] if f"{s}__den" in df.columns]


def _sigma2(pa, na, pb, nb) -> float:
    m = (na >= MIN_UNITS) & (nb >= MIN_UNITS)
    return float(np.mean((pa[m] - pb[m]) ** 2 / (1 / na[m] + 1 / nb[m]))) if m.sum() >= 10 else np.nan


def _tau2(p, n, sigma2) -> tuple[float, float]:
    m = n >= MIN_UNITS
    p, n = p[m], n[m]
    big_n = n.sum()
    if len(p) < 10 or not np.isfinite(sigma2):
        return float((n * p).sum() / big_n) if big_n > 0 else np.nan, np.nan
    mu = float((n * p).sum() / big_n)
    s = (n * (p - mu) ** 2).sum()
    return mu, float((s - (len(p) - 1) * sigma2) / (big_n - (n ** 2).sum() / big_n))


def _season_units(games: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    return _units(games.group_by(by).agg(pl.col([x for x in COUNTS if x in games.columns]).sum()))


def _halves(games: pl.DataFrame, matches: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """-> (odd games, even games) of each player and team, row-aligned."""
    keys = ["team_season_id", "player_id"]
    g = games.join(matches.select("match_id", "start_utc"), on="match_id").sort("start_utc", "match_id")
    g = g.with_columns(half=(pl.int_range(pl.len()).over(keys) % 2))
    half = _season_units(g, [*keys, "half"])
    both = half.filter(c("half") == 0).select(keys).join(half.filter(c("half") == 1).select(keys), on=keys)
    return (both.join(half.filter(c("half") == 0), on=keys, how="left"),
            both.join(half.filter(c("half") == 1), on=keys, how="left"))


def reliability(games: pl.DataFrame, matches: pl.DataFrame, qualify_min: float) -> pl.DataFrame:
    """One row per stat: sampling noise, true spread, stabilization point k (with a bootstrap
    90% interval), k in games of a regular rotation player, observed vs implied split-half r."""
    odd, even = _halves(games, matches)
    full = _season_units(games, ["team_season_id", "player_id"])
    rng = np.random.default_rng(SEED)
    per_game = full.filter(c("seconds") / 60 >= qualify_min)
    season_games = float(games.select("match_id", "team_season_id").unique().group_by("team_season_id").len()["len"]
                         .median())
    rows = []
    for s in _available(full):
        (pa, na), (pb, nb) = _pn(odd, s), _pn(even, s)
        p, n = _pn(full, s)
        sigma2 = _sigma2(pa, na, pb, nb)
        mu, tau2 = _tau2(p, n, sigma2)
        k = sigma2 / tau2 if tau2 > 0 else (np.nan if np.isnan(tau2) else np.inf)
        boot = []
        for _ in range(BOOT):
            i, j = rng.integers(0, len(pa), len(pa)), rng.integers(0, len(p), len(p))
            s2 = _sigma2(pa[i], na[i], pb[i], nb[i])
            _, t2 = _tau2(p[j], n[j], s2)
            boot.append(s2 / t2 if t2 > 0 else np.inf)
        boot = np.nan_to_num(np.array(boot), nan=np.inf)
        lo, hi = np.quantile(boot, [0.05, 0.95], method="nearest")
        _, nq = _pn(per_game, s)
        gq = per_game["games"].to_numpy()
        units_pg = float(np.median(nq[nq > 0] / gq[nq > 0])) if (nq > 0).any() else np.nan
        n_med = float(np.median(nq[nq > 0])) if (nq > 0).any() else np.nan
        # split-half check among players with a quarter of k per half (the implied r averages the
        # noise over players; with many tiny samples a plain correlation is not comparable)
        floor = max(MIN_UNITS, k / 4) if np.isfinite(k) else MIN_UNITS
        both = (na >= floor) & (nb >= floor)
        varies = both.sum() >= 10 and pa[both].std() > 0 and pb[both].std() > 0
        r_obs = float(np.corrcoef(pa[both], pb[both])[0, 1]) if varies else np.nan
        # what the model implies for the same players: noise variance sigma^2 * mean(1/n) per half
        ia, ib = ((1 / na[both]).mean(), (1 / nb[both]).mean()) if both.any() else (np.nan, np.nan)
        r_model = float(tau2 / np.sqrt((tau2 + sigma2 * ia) * (tau2 + sigma2 * ib))) if tau2 > 0 else 0.0
        stat = next((x for x in STATS if x.name == s), None)
        rows.append({
            "stat": s, "label": stat.label if stat else "On/off net", "unit": UNIT[s], "box": bool(stat and stat.box),
            "shown": stat.shown if stat else True, "players": int((n >= MIN_UNITS).sum()), "split_players": int(both.sum()),
            "mu": mu, "sigma": float(np.sqrt(sigma2)), "tau": float(np.sqrt(max(tau2, 0))),
            "k": k, "k_lo": float(lo), "k_hi": float(hi),
            "units_per_game": units_pg, "k_games": k / units_pg if units_pg else np.nan,
            "season_units": n_med, "season_reliability": n_med / (n_med + k) if np.isfinite(k) else 0.0,
            "split_r": r_obs, "split_r_model": r_model, "season_games": season_games,
        })
    return pl.DataFrame(rows)


def career_prior(career: pl.DataFrame, season_key: str) -> pl.DataFrame:
    """TBF career totals of the season before `season_key` in league competitions, as counts:
    one row per player and source ('TBL' or 'other'). Shooting is null where TBF lacks it."""
    start = int(season_key[:4])
    prev = f"{start - 1}-{start}"
    cr = career.filter((c("career_season") == prev) & c("competition").is_in(SENIOR)).unique(
        ["player_id", "competition", "team_name"])
    shoot = ["fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta"]
    cr = cr.with_columns(*[pl.when(~c("shooting_missing")).then(c(x)) for x in shoot])
    return cr.with_columns(source=pl.when(c("competition") == "TBL").then(pl.lit("TBL")).otherwise(pl.lit("other"))
                           ).group_by("player_id", "source").agg(
        pl.col("pts", "oreb", "dreb", "ast", "tov", "stl", "blk", "seconds", "games").sum(),
        *[pl.when(c(x).is_not_null().any()).then(c(x).sum()).alias(x) for x in shoot]
    ).with_columns(fgm=c("fg2m") + c("fg3m"), fga=c("fg2a") + c("fg3a"))


def _shrink(p, n, mu, k, priors) -> np.ndarray:
    """(n p + sum d n_prev p_prev + k mu) / (n + sum d n_prev + k); priors: [(d, p_prev, n_prev)]."""
    num, den = n * p + k * mu, n + k
    for d, pp, nn in priors:
        num, den = num + d * nn * pp, den + d * nn
    return num / den


def _player_order(games: pl.DataFrame, matches: pl.DataFrame) -> pl.DataFrame:
    g = games.join(matches.select("match_id", "start_utc"), on="match_id").sort("start_utc", "match_id")
    return g.with_columns(nth=pl.int_range(pl.len()).over("player_id"))


def _backtest_arrays(games: pl.DataFrame, matches: pl.DataFrame, prior: pl.DataFrame, rel: pl.DataFrame):
    """-> {(stat, G): dict of arrays}: first G games, the rest of the season, last season by source."""
    g = _player_order(games, matches)
    ids = g.select("player_id").unique().sort("player_id")
    wide = {src: ids.join(_units(prior.filter(c("source") == src)), on="player_id", how="left")
            for src in ("TBL", "other")}
    rel = {r["stat"]: r for r in rel.iter_rows(named=True)}
    out = {}
    for G in BACKTEST_GAMES:
        first = ids.join(_season_units(g.filter(c("nth") < G), ["player_id"]), on="player_id", how="left")
        rest = ids.join(_season_units(g.filter(c("nth") >= G), ["player_id"]), on="player_id", how="left")
        for s in (x for x in STATS if x.box and x.name in rel):
            (p, n), (pr, nr) = _pn(first, s.name), _pn(rest, s.name)
            (pt, nt), (po, no) = _pn(wide["TBL"], s.name), _pn(wide["other"], s.name)
            m = nr >= BACKTEST_MIN_REST
            if m.sum() < 10 or not np.isfinite(rel[s.name]["k"]):
                continue
            out[(s.name, G)] = {"idx": np.flatnonzero(m), "players": len(m), "p": p[m], "n": n[m], "pr": pr[m], "w": nr[m], "pt": pt[m], "nt": nt[m],
                                "po": po[m], "no": no[m], "mu": rel[s.name]["mu"], "k": rel[s.name]["k"],
                                "sigma": rel[s.name]["sigma"]}
    return out


def _grid_mse(a: dict, weights: np.ndarray | None = None) -> np.ndarray:
    """Weighted MSE of the padded estimate for every (d_TBL, d_other) on the grid."""
    dt, do = np.meshgrid(D_GRID, D_GRID, indexing="ij")
    dt, do = dt[..., None], do[..., None]
    pred = ((a["n"] * a["p"] + a["k"] * a["mu"] + dt * a["nt"] * a["pt"] + do * a["no"] * a["po"])
            / (a["n"] + a["k"] + dt * a["nt"] + do * a["no"]))
    w = a["w"] if weights is None else a["w"] * weights
    return (w * (a["pr"] - pred) ** 2).sum(-1) / w.sum()


def backtest(games: pl.DataFrame, matches: pl.DataFrame, prior: pl.DataFrame,
             rel: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Predict each player's rate over the rest of the season from the first G games: raw, padded
    with league average, and padded with last season's TBF totals too (weights d_TBL, d_other per
    group, chosen to minimise the summed relative MSE over the group's rates and G = 2, 4, 8).
    -> (one row per stat and G with weighted RMSEs, one row per group with d and a bootstrap
    90% interval over players). Weights = units in the rest of the season."""
    arrays = _backtest_arrays(games, matches, prior, rel)
    rng = np.random.default_rng(SEED)
    n_players = next(iter(arrays.values()))["players"] if arrays else 0
    rows, drows = [], []
    for group, label in GROUPS.items():
        keys = [k for k in arrays if next(x for x in STATS if x.name == k[0]).group == group]
        if not keys:
            continue
        grids = {k: _grid_mse(arrays[k]) for k in keys}
        total = sum(g / g[0, 0] for g in grids.values())
        i, j = np.unravel_index(np.argmin(total), total.shape)
        boot = []
        for _ in range(BOOT // 4):
            # resample players: the same draw for every rate (each rate keeps its own eligible players)
            draw = rng.integers(0, n_players, n_players)
            counts = np.bincount(draw, minlength=n_players)
            b = sum((g := _grid_mse(arrays[k], counts[arrays[k]["idx"]])) / g[0, 0] for k in keys)
            bi, bj = np.unravel_index(np.argmin(b), b.shape)
            boot.append((D_GRID[bi], D_GRID[bj]))
        boot = np.array(boot)
        drows.append({"group": group, "label": label, "d_tbl": float(D_GRID[i]), "d_other": float(D_GRID[j]),
                      "d_tbl_lo": float(np.quantile(boot[:, 0], 0.05)), "d_tbl_hi": float(np.quantile(boot[:, 0], 0.95)),
                      "d_other_lo": float(np.quantile(boot[:, 1], 0.05)), "d_other_hi": float(np.quantile(boot[:, 1], 0.95))})
        for (stat, G) in keys:
            a, grid = arrays[(stat, G)], grids[(stat, G)]
            has = (a["nt"] > 0) | (a["no"] > 0)
            raw = np.where(a["n"] > 0, a["p"], a["mu"])
            rmse = lambda pred, m=slice(None): float(np.sqrt((a["w"][m] * (a["pr"][m] - pred[m]) ** 2).sum()  # noqa: E731
                                                             / a["w"][m].sum()))
            sub = {x: v[has] if isinstance(v, np.ndarray) and len(v) == len(has) else v for x, v in a.items()}
            sg = _grid_mse(sub) if has.any() else np.full_like(grid, np.nan)
            # 90% intervals: what happened next = estimate +- (its own error and the rest's sampling noise)
            dt, do = D_GRID[i], D_GRID[j]
            total = a["n"] + a["k"] + dt * a["nt"] + do * a["no"]
            est = (a["n"] * a["p"] + a["k"] * a["mu"] + dt * a["nt"] * a["pt"] + do * a["no"] * a["po"]) / total
            band = 1.645 * a["sigma"] * np.sqrt(1 / total + 1 / a["w"])
            rows.append({"stat": stat, "group": group, "games": G, "players": len(a["p"]), "with_prior": int(has.sum()),
                         "with_tbl_prior": int((a["nt"] > 0).sum()),
                         "rmse_league": rmse(np.full(len(a["p"]), a["mu"])), "rmse_raw": rmse(raw),
                         "rmse_padded": float(np.sqrt(grid[0, 0])), "rmse_prior": float(np.sqrt(grid[i, j])),
                         "rmse_raw_with_prior": rmse(raw, has) if has.any() else np.nan,
                         "rmse_padded_with_prior": float(np.sqrt(sg[0, 0])), "rmse_prior_with_prior": float(np.sqrt(sg[i, j])),
                         "coverage_90": float((np.abs(a["pr"] - est) <= band).mean())})
    return pl.DataFrame(rows), pl.DataFrame(drows)


def stable_rates(games: pl.DataFrame, rel: pl.DataFrame, d: pl.DataFrame, career: pl.DataFrame | None,
                 prior_games: pl.DataFrame | None) -> pl.DataFrame:
    """Each player's rates padded with league average and last season, one row per player,
    team and stat: the raw rate and its units, last season's rate, units and source, the
    estimate with a 90% interval, and how much of it comes from this season, last season
    and the league average.

    Last season = our own play-by-play season (every rate; d_TBL) if there is one, else TBF's
    TBL totals (box-score rates only; d_TBL), plus TBF's totals in other leagues (box-score
    rates; d_other). `rel` (k, sigma) is ideally last season's: a full season pins k down."""
    keys = ["team_season_id", "player_id"]
    cur = _season_units(games, keys)
    ids = cur.select("player_id").unique()
    tbl = other = None
    if prior_games is not None:
        tbl = ids.join(_season_units(prior_games, ["player_id"]), on="player_id")
    if career is not None:
        if tbl is None:
            tbl = ids.join(_units(career.filter(c("source") == "TBL")), on="player_id")
        other = ids.join(_units(career.filter(c("source") == "other")), on="player_id")
    tbl, other = [cur.select(keys).join(x, on="player_id", how="left") if x is not None else None for x in (tbl, other)]
    rel = {r["stat"]: r for r in rel.iter_rows(named=True)}
    dmap = {r["group"]: (r["d_tbl"], r["d_other"]) for r in d.iter_rows(named=True)} if d.height else {}
    out = []
    for name in [x for x in _available(cur) if x in rel and np.isfinite(rel[x]["k"])
                 and next((s.shown for s in STATS if s.name == x), True)]:
        stat = next((s for s in STATS if s.name == name), None)
        k, sigma = rel[name]["k"], rel[name]["sigma"]
        p, n = _pn(cur, name)
        mu = float((n * p).sum() / n.sum()) if n.sum() > 0 else rel[name]["mu"]
        d_tbl, d_other = dmap.get(stat.group, (0.0, 0.0)) if stat else (0.0, 0.0)
        zero = (np.zeros(len(p)), np.zeros(len(p)))
        pt, nt = _pn(tbl, name) if d_tbl > 0 and tbl is not None and f"{name}__den" in tbl.columns else zero
        po, no = _pn(other, name) if d_other > 0 and other is not None and f"{name}__den" in other.columns else zero
        est = _shrink(p, n, mu, k, [(d_tbl, pt, nt), (d_other, po, no)])
        total = n + d_tbl * nt + d_other * no + k
        se = sigma / np.sqrt(total)
        # shares (FT%, USG%, BLK%, ...) stay within 0-1; eFG%, TS%, FT rate, shot quality, POE, on/off need not
        lo, hi = est - 1.645 * se, est + 1.645 * se
        if name not in UNBOUNDED:
            lo, hi = np.clip(lo, 0, 1), np.clip(hi, 0, 1)
        n_prev = d_tbl * nt + d_other * no
        p_prev = np.divide(d_tbl * nt * pt + d_other * no * po, n_prev, out=np.full(len(p), np.nan), where=n_prev > 0)
        src = np.where((nt > 0) & (no > 0), "TBL + diğer lig", np.where(nt > 0, "TBL", np.where(no > 0, "diğer lig", "")))
        out.append(cur.select(keys).with_columns(
            stat=pl.lit(name), raw=pl.Series(np.where(n > 0, p, np.nan)).fill_nan(None), units=pl.Series(n),
            prior=pl.Series(p_prev).fill_nan(None), prior_units=pl.Series(nt + no), prior_source=pl.Series(src),
            league_rate=pl.lit(mu), estimate=pl.Series(est), lo=pl.Series(lo), hi=pl.Series(hi),
            w_season=pl.Series(n / total), w_prior=pl.Series(n_prev / total), w_league=pl.Series(k / total)))
    return pl.concat(out) if out else pl.DataFrame(schema={**dict.fromkeys(keys, pl.Int64), "stat": pl.String})
