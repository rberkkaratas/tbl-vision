from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from tbl_data import validate as V
from tbl_data.analysis import stability as S
from tbl_data.analysis import xpts

BOX = ["pts", "fgm", "fga", "fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "tov", "stl", "blk"]


def season(talent: np.ndarray, games: int = 30, fta: float = 4.0, seed: int = 1) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Box-score game logs of players with a true FT% each: (player_games, matches)."""
    rng = np.random.default_rng(seed)
    rows = []
    for pid, t in enumerate(talent):
        for g in range(games):
            a = int(rng.poisson(fta))
            m = int(rng.binomial(a, t))
            rows.append({"match_id": g, "team_season_id": 1, "player_id": pid, "games": 1, "seconds": 1500,
                         **dict.fromkeys(BOX, 0), "fta": a, "ftm": m, "pts": m})
    start = datetime(2025, 10, 1, tzinfo=UTC)
    matches = pl.DataFrame({"match_id": range(games), "start_utc": [start + timedelta(days=7 * g) for g in range(games)]})
    return pl.DataFrame(rows), matches


def talents(n: int, mu: float, k: float, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).beta(mu * k, (1 - mu) * k, n)


def ft(rel: pl.DataFrame) -> dict:
    return rel.filter(pl.col("stat") == "ft_pct").row(0, named=True)


def test_reliability_recovers_the_stabilization_point():
    # beta-binomial: true FT% ~ Beta(mu k, (1 - mu) k), so k free throws are half signal
    t = talents(800, 0.72, 20)
    games, matches = season(t)
    r = ft(S.reliability(games, matches, qualify_min=100))
    assert 0.70 < r["mu"] < 0.74
    assert abs(r["sigma"] ** 2 / np.mean(t * (1 - t)) - 1) < 0.15  # split halves recover the binomial noise
    assert 13 < r["k"] < 30 and r["k_lo"] < r["k"] < r["k_hi"]
    assert abs(r["split_r"] - r["split_r_model"]) < 0.1
    assert abs(r["k_games"] - r["k"] / 4) < 2  # four free throws a game


def test_backtest_trusts_last_season_only_when_it_carries_over():
    talent = talents(400, 0.72, 20, seed=0)
    games, matches = season(talent, seed=1)
    rel = S.reliability(games, matches, qualify_min=100)

    def prior(t: np.ndarray, seed: int) -> pl.DataFrame:
        last, _ = season(t, seed=seed)
        return last.group_by("player_id").agg(pl.col(*BOX, "seconds", "games").sum()).with_columns(source=pl.lit("TBL"))

    same = S.backtest(games, matches, prior(talent, 2), rel)[1]
    other = S.backtest(games, matches, prior(talents(400, 0.72, 20, seed=9), 2), rel)[1]
    d_same = same.filter(pl.col("group") == "isabet")["d_tbl"][0]
    d_other = other.filter(pl.col("group") == "isabet")["d_tbl"][0]
    assert d_same >= 0.6 and d_other <= 0.2


def test_padded_rates_sit_between_their_sources():
    talent = talents(200, 0.72, 20)
    games, matches = season(talent, games=4)
    rel = S.reliability(*season(talent, seed=3), qualify_min=100)
    last, _ = season(talent, seed=4)
    career = last.group_by("player_id").agg(pl.col(*BOX, "seconds", "games").sum()).with_columns(source=pl.lit("other"))
    d = pl.DataFrame({"group": ["isabet"], "d_tbl": [1.0], "d_other": [0.5]})
    stable = S.stable_rates(games, rel, d, career, None)
    t = stable.filter(pl.col("stat") == "ft_pct")
    assert t.height == 200 and t["prior_source"].unique().to_list() == ["diğer lig"]
    assert V.player_stable({"player_stable": stable}).ok
    # four games of a 20-k rate: the prior and the league weigh in
    assert t["w_season"].median() < 0.6 and t["w_prior"].median() > 0.2


def test_calibration_scores_a_calibrated_model():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.2, 0.8, 20000)
    y = rng.binomial(1, p)
    s = xpts._scores(y, p, np.full(len(p), 2))
    assert abs(s["calibration_slope"] - 1) < 0.1 and abs(s["calibration_intercept"]) < 0.1
    over = xpts._scores(y, np.clip(0.5 + 1.5 * (p - 0.5), 0.01, 0.99), np.full(len(p), 2))
    assert over["calibration_slope"] < 0.8  # overconfident predictions -> slope below 1
