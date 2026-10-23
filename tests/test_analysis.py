import polars as pl
import pytest
from conftest import needs_fixtures

from tbl_data import validate as V
from tbl_data.analysis import analyze, flow, pairs, xpts
from tbl_data.enrich import enrich

H, A = 1, 2
MT = pl.DataFrame({"match_id": [9, 9], "team_season_id": [H, A], "is_home": [True, False]})


def events(rows):
    """rows: (seq, period, elapsed_s, team or None, action, success)."""
    return pl.DataFrame([{"match_id": 9, "seq": s, "action_number": s, "period_ord": p, "elapsed_s": float(t),
                          "team_season_id": tm, "player_id": None if tm is None else tm * 10, "action_type": a,
                          "success": ok} for s, p, t, tm, a, ok in rows])


def test_flow_matches_fiba_livestats_definitions():
    ev = events([
        (1, 1, 10, H, "2pt", True),        # 2-0  H leads
        (2, 1, 20, A, "3pt", True),        # 2-3  lead change
        (3, 1, 30, H, "freethrow", True),  # 3-3  tie
        (4, 1, 40, A, "2pt", False),
        (5, 2, 700, H, "2pt", True),       # 5-3  (Q1 ended level: another tie)
        (6, 2, 710, H, "3pt", True),       # 8-3
        (7, 2, 720, H, "3pt", True),       # 11-3: a 9-0 run for H (FT, 2, 3, 3)
        (8, 2, 1100, A, "2pt", True),      # 11-5
    ])
    tl = flow.score_timeline(ev, MT)
    g, runs = flow.game_flow(tl, MT)
    h = g.filter(pl.col("team_season_id") == H).row(0, named=True)
    a = g.filter(pl.col("team_season_id") == A).row(0, named=True)
    assert h["times_tied"] == 2 and h["lead_changes_game"] == 2 and (h["lead_changes"], a["lead_changes"]) == (1, 1)
    assert (h["biggest_lead"], a["biggest_lead"]) == (8, 1)
    assert h["biggest_run"] == 9 and runs.height == 1 and runs["margin_before"][0] == -1
    # H led 10-20 s, A 20-30 s, level until 700 s, then H to the end (1200 s)
    assert (h["time_leading_s"], a["time_leading_s"]) == (10 + 500, 10)


def test_pairs_split_together_and_apart():
    st = pl.DataFrame({"match_id": [9, 9], "team_season_id": [H, H], "lineup": [[1, 2, 3, 4, 5], [1, 3, 4, 5, 6]],
                       "seconds": [100.0, 50.0], "pts_for": [10, 4], "pts_against": [5, 6],
                       "poss_for": [5, 3], "poss_against": [5, 3]})
    p = pairs.pair_on_court(st)
    r = p.filter((pl.col("player_a") == 1) & (pl.col("player_b") == 2)).row(0, named=True)
    assert (r["seconds"], r["a_without_b_seconds"], r["b_without_a_seconds"]) == (100.0, 50.0, 0.0)
    assert r["net"] == 100.0 and r["a_without_b_net"] == pytest.approx(-66.7)
    assert r["b_without_a_net"] is None  # no possessions: no rating, not NaN


def test_xpts_leaves_out_outcome_tags():
    assert not {"fast_break", "assisted", "blocked", "fouled", "sub_type"} & set(xpts.NUMERIC)


@needs_fixtures
def test_analyze_fixture_match(staged):
    e = enrich(staged.tables).tables
    a = analyze(staged.tables, e)
    checks = {c.name: c for c in V.run_analysis(staged.tables, e, a.tables, a.metrics)}
    for name in ("game_flow_vs_genius", "score_timeline_final", "pairs_consistency", "assist_pairs_total"):
        assert checks[name].ok, (name, checks[name].failures)
    assert a.metrics["xpts"]["out_of_fold"] is False  # one game: nothing to hold out
