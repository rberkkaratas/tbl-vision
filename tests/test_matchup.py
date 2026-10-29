import numpy as np
import polars as pl
import pytest

from tbl_data.analysis import matchup


def games(rows):
    """rows: (match_id, team, opp, is_home, pts, poss) -> team-game rows as team_games() gives them."""
    df = pl.DataFrame([{"match_id": m, "team_season_id": t, "opp_id": o, "is_home": h, "pts": p, "poss": n}
                       for m, t, o, h, p, n in rows], schema_overrides={"team_season_id": pl.Int32, "opp_id": pl.Int32})
    return df


def round_robin(strength: dict[int, float], home: float, reps: int = 30):
    """Every pair meets home and away `reps` times; ppp = 1 + strength[t] - strength[o]/2 ± home."""
    rows, m = [], 0
    for _ in range(reps):
        for a in strength:
            for b in strength:
                if a == b:
                    continue
                m += 1
                for t, o, h in ((a, b, True), (b, a, False)):
                    ppp = 1.0 + strength[t] - strength[o] / 2 + (home if h else -home)
                    rows.append((m, t, o, h, round(ppp * 100), 100))
    return games(rows)


def test_ratings_recover_strength_and_home_court():
    g = round_robin({1: 0.10, 2: 0.0, 3: -0.10}, home=0.02)
    mu, off, de, h = matchup.fit_ratings(g, lam=1.0)
    assert h == pytest.approx(0.02, abs=0.002)
    assert off[1] > off[2] > off[3] and de[1] < de[2] < de[3]  # a strong team scores more and allows less
    assert sum(off.values()) == pytest.approx(0, abs=1e-6)  # centred on the league average


def test_ridge_shrinks_toward_league_average():
    g = round_robin({1: 0.10, 2: 0.0, 3: -0.10}, home=0.0, reps=1)
    _, weak, _, _ = matchup.fit_ratings(g, lam=1.0)
    _, strong, _, _ = matchup.fit_ratings(g, lam=10_000.0)
    assert abs(strong[1]) < abs(weak[1])


def test_projection_is_symmetric_on_a_neutral_court():
    off, de, pace = {1: 0.05, 2: -0.05}, {1: -0.03, 2: 0.03}, {1: 80.0, 2: 70.0}
    p, ea, eb = matchup.project(1.0, off, de, 0.02, pace, 75.0, 1, 2, 0)
    p2, eb2, ea2 = matchup.project(1.0, off, de, 0.02, pace, 75.0, 2, 1, 0)
    assert p == pytest.approx(80 * 70 / 75) and p == p2
    assert (ea, eb) == pytest.approx((ea2, eb2))
    _, home_a, home_b = matchup.project(1.0, off, de, 0.02, pace, 75.0, 1, 2, 1)
    assert home_a - home_b == pytest.approx(ea - eb + 0.04)  # home court counts on both ends


def test_icc_separates_signal_from_noise():
    rng = np.random.default_rng(1)
    rows = []
    for t in range(16):
        true = rng.normal(0, 1)
        for _ in range(30):
            rows.append({"team_season_id": t, "signal": true + rng.normal(0, 1), "noise": rng.normal(0, 1)})
    df = pl.DataFrame(rows)
    rel = {r["stat"]: r for r in matchup.team_reliability(df, {"signal": "Signal", "noise": "Noise"}).iter_rows(named=True)}
    assert rel["signal"]["icc"] == pytest.approx(0.5, abs=0.2) and rel["noise"]["icc"] < 0.1
    tiers = {r["stat"]: r["tier"] for r in matchup.with_tiers(pl.DataFrame(list(rel.values())), 8).iter_rows(named=True)}
    assert tiers == {"signal": "oturdu", "noise": "gürültü"}


def test_factor_matchup_is_multiplicative_and_signed_for_team_a():
    t = pl.DataFrame({"team_season_id": [1, 2], **{k: [0.55, 0.45] for k in ("efg", "orb", "ftr", "r3")},
                      "tov": [0.15, 0.15], **{f"d_{k}": [0.50, 0.50] for k in ("efg", "tov", "orb", "ftr", "r3")}})
    lg = {"efg": 0.5, "tov": 0.15, "orb": 0.5, "ftr": 0.5, "r3": 0.5}
    f = matchup.matchup_factors(t, lg, {"efg": 1.5, "tov": -1.2, "orb": 0.6, "ftr": 0.25})
    off = f.filter((pl.col("team_season_id") == 1) & (pl.col("direction") == "offense") & (pl.col("factor") == "efg")).row(0, named=True)
    assert off["expected"] == pytest.approx(0.55 * 0.50 / 0.5) and off["impact"] == pytest.approx(1.5 * 5)
    d = f.filter((pl.col("team_season_id") == 1) & (pl.col("direction") == "defense") & (pl.col("factor") == "efg")).row(0, named=True)
    assert d["impact"] == pytest.approx(1.5 * 5)  # team 2 shoots below the league: good for team 1
    assert off["att_rank"] == 1 and d["att_rank"] == 2
