import polars as pl
import pytest
from conftest import needs_fixtures

from tbl_data import validate as V
from tbl_data.enrich import enrich
from tbl_data.enrich.ft_trips import build_trips
from tbl_data.enrich.order import apply_overrides, order_events
from tbl_data.enrich.possessions import build_possessions
from tbl_data.enrich.shots import locate, zone

H, A = 1, 2  # team ids in synthetic games


def ev(an, t, action, sub=None, team=None, player=None, ok=True, prev=None, q=(), period=1, score=(0, 0)):
    return {"match_id": 9, "action_number": an, "period_ord": period, "period_label": f"Q{period}",
            "elapsed_s": float(t), "clock": "", "clock_s": 600.0 - t, "action_type": action, "sub_type": sub,
            "team_season_id": team, "is_home": None if team is None else team == H, "player_id": player,
            "success": ok, "previous_action": prev, "qualifiers": list(q), "score_home": score[0], "score_away": score[1]}


def run(events):
    events, _ = order_events(events)
    trips, trip_of = build_trips(9, events)
    poss, anomalies = build_possessions(9, events, trips, trip_of, (H, A))
    return events, trips, poss, anomalies


def test_order_puts_late_entries_by_time_and_links_after_targets():
    out, moves = order_events([
        ev(1, 0, "period", "start"),
        ev(3, 10, "rebound", "defensive", A, 20, prev=4),  # entered before the shot it refers to
        ev(4, 10, "2pt", "jumpshot", H, 10, ok=False),
        ev(2, 30, "3pt", "jumpshot", A, 21),  # entered early, happened later
    ])
    assert [e["action_number"] for e in out] == [1, 4, 3, 2]
    assert moves == [] and [e["seq"] for e in out] == [1, 2, 3, 4]


def test_order_uses_score_stamp_inside_a_second():
    # the substitution was entered after the free throw but stamped with the score before it
    out, _ = order_events([ev(1, 5, "freethrow", "1of1", H, 10, score=(1, 0)),
                           ev(2, 5, "substitution", "out", A, 20, score=(0, 0))])
    assert [e["action_number"] for e in out] == [2, 1]


def test_rebound_with_mistyped_clock_goes_back_to_its_shot():
    out, moved = order_events([
        ev(1, 0, "period", "start"),
        ev(2, 597, "3pt", "jumpshot", A, 20, ok=False),
        ev(3, 600, "rebound", "defensive", H, 10, prev=2),  # typed 00:00 at the buzzer
        ev(4, 599.9, "2pt", "layup", H, 11, ok=False),
    ])
    assert [e["action_number"] for e in out] == [1, 2, 3, 4]
    assert out[2]["elapsed_s"] == 599.9 and [m["action_number"] for m in moved] == [3]


def test_overrides_move_actions_after_anchor():
    events, _ = order_events([ev(1, 5, "foul", "personal", A, 20), ev(2, 4, "substitution", "out", H, 11),
                              ev(3, 6, "2pt", "layup", H, 10)])
    out, log = apply_overrides(events, {"reorder": [{"actions": [2], "after": 1, "reason": "clock typo"}]})
    assert [e["action_number"] for e in out] == [1, 2, 3]
    assert out[1]["elapsed_s"] == 5 and log[0]["reason"] == "clock typo"


def test_trip_survives_interleaved_technical():
    _, trips, _, _ = run([
        ev(1, 0, "period", "start"),
        ev(2, 5, "foul", "personal", A, 20, q=["shooting", "2freethrow"]),
        ev(3, 5, "freethrow", "1of2", H, 10),
        ev(4, 5, "foul", "coachTechnical", A, q=["1freethrow"]),
        ev(5, 5, "freethrow", "1of1", H, 11),
        ev(6, 5, "freethrow", "2of2", H, 10, ok=False),
    ])
    assert [(t["shooter_id"], t["attempts"], t["trip_type"], t["complete"]) for t in trips] == [
        (10, 2, "shooting_2pt", True), (11, 1, "technical", True)]


def test_possession_rules():
    events, _, poss, anomalies = run([
        ev(1, 0, "period", "start"),
        ev(2, 8, "2pt", "layup", H, 10),                                     # H scores: ends
        ev(3, 20, "3pt", "jumpshot", A, 20, ok=False),
        ev(4, 21, "rebound", "offensive", A, 21, prev=3),                     # same possession
        ev(5, 25, "2pt", "layup", A, 21),                                     # and-one: continues
        ev(6, 25, "foul", "personal", H, 11, q=["shooting", "1freethrow"]),
        ev(7, 25, "freethrow", "1of1", A, 21, ok=False),
        ev(8, 26, "rebound", "defensive", H, 12, prev=7),                     # ends A
        ev(9, 30, "foul", "technical", A, 22, q=["1freethrow"]),
        ev(10, 30, "freethrow", "1of1", H, 10),                               # technical: H keeps ball
        ev(11, 40, "turnover", "badpass", H, 10),
        ev(12, 40, "steal", None, A, 20, prev=11),
        ev(13, 45, "2pt", "dunk", A, 20),
        ev(14, 50, "period", "end"),
    ])
    assert anomalies == []
    assert [(p["team_season_id"], p["start_type"], p["end_type"], p["pts"], p["oreb"]) for p in poss] == [
        (H, "period_start", "made_fg", 2, 0),
        (A, "after_made_fg", "defensive_rebound", 2, 1),
        (H, "after_defensive_rebound", "turnover", 1, 0),
        (A, "after_steal", "made_fg", 2, 0),
        (H, "after_made_fg", "period_end", 0, 0),  # 5 s with the ball before the buzzer
    ]
    assert {e["action_number"]: e["possession_no"] for e in events}[10] == 3


def test_shot_inside_ft_trip_moves_before_the_foul():
    out, moved = order_events([
        ev(1, 5, "foul", "personal", A, 20, q=["2freethrow"]),
        ev(2, 5, "foulon", None, H, 10, prev=1),
        ev(3, 5, "freethrow", "1of2", H, 10, ok=False, q=["2ndchance"]),
        ev(4, 5, "freethrow", "2of2", H, 10, q=["2ndchance"], score=(1, 0)),
        ev(5, 5, "2pt", "layup", H, 10, ok=False),  # entered late, stamped before the trip
        ev(6, 5, "rebound", "offensive", H, prev=5),
    ])
    assert [e["action_number"] for e in out] == [5, 6, 1, 2, 3, 4] and len(moved) == 2


def test_late_and_one_and_described_actions():
    events, trips, poss, anomalies = run([
        ev(1, 0, "period", "start"),
        ev(2, 10, "2pt", "layup", H, 10),
        ev(3, 10, "assist", None, H, 11, prev=2),
        ev(4, 12, "foul", "personal", A, 20, q=["shooting", "1freethrow"]),  # entered 2 s late
        ev(5, 12, "substitution", "out", A, 21),
        ev(6, 12, "substitution", "in", A, 22),
        ev(7, 12, "freethrow", "1of1", H, 10, ok=False),
        ev(8, 13, "rebound", "defensive", A, 20, prev=7),
        ev(9, 20, "turnover", "badpass", A, 20),
        ev(10, 20, "steal", None, H, 11, prev=9),
        ev(11, 25, "period", "end"),
    ])
    assert anomalies == [] and trips[0]["trip_type"] == "and_one"
    assert [(p["team_season_id"], p["end_type"], p["pts"]) for p in poss] == [
        (H, "defensive_rebound", 2), (A, "turnover", 0), (H, "period_end", 0)]
    by = {e["action_number"]: e for e in events}
    assert by[3]["possession_no"] == 1 and by[10]["possession_no"] == 2  # assist/steal stay with their target


def test_held_ball_after_a_change_is_its_own_possession():
    _, _, poss, anomalies = run([
        ev(1, 0, "period", "start"),
        ev(2, 5, "turnover", "travel", H, 10),
        ev(3, 9, "jumpball", "heldball", A, 20),  # arrow goes back to H
        ev(4, 15, "3pt", "jumpshot", H, 11),
    ])
    assert anomalies == []
    assert [(p["team_season_id"], p["end_type"]) for p in poss] == [(H, "turnover"), (A, "jumpball"), (H, "made_fg")]


def test_stint_possessions_skip_the_outgoing_five():
    from tbl_data.enrich import _stint_possessions
    stints = [{"team_season_id": H, "period_ord": 1, "start_seq": 1}, {"team_season_id": H, "period_ord": 1, "start_seq": 4}]
    poss = [{"possession_no": 1, "team_season_id": H, "period_ord": 1, "start_seq": 1},
            {"possession_no": 2, "team_season_id": A, "period_ord": 1, "start_seq": 3}]
    stints[0]["lineup"], stints[1]["lineup"] = [1, 2, 3, 4, 5], [1, 2, 3, 4, 6]
    old, new = {"home_lineup": [1, 2, 3, 4, 5], "away_lineup": []}, {"home_lineup": [1, 2, 3, 4, 6], "away_lineup": []}
    events = [{"seq": 1, "possession_no": 1, "action_type": "period", "is_home": None, **old},
              {"seq": 2, "possession_no": 1, "action_type": "2pt", "is_home": True, **old},
              {"seq": 3, "possession_no": 2, "action_type": "substitution", "is_home": True, **new},  # dead ball
              {"seq": 4, "possession_no": 2, "action_type": "substitution", "is_home": True, **new},
              {"seq": 5, "possession_no": 2, "action_type": "3pt", "is_home": False, **new}]
    _stint_possessions(stints, poss, events, H)
    assert [(s["poss_for"], s["poss_against"]) for s in stints] == [(1, 0), (0, 1)]


def test_switch_without_end_is_logged():
    _, _, poss, anomalies = run([
        ev(1, 0, "period", "start"),
        ev(2, 5, "3pt", "jumpshot", H, 10, ok=False),
        ev(3, 9, "2pt", "layup", A, 20),  # no rebound logged in between
    ])
    assert [p["team_season_id"] for p in poss] == [H, A]
    assert [a["action_number"] for a in anomalies] == [3]


def test_shot_location_rotates_far_end():
    near = locate({"x": 5.625, "y": 50.0, "shot_type": "2pt", "sub_type": "layup"}, high_end=False)
    far = locate({"x": 94.375, "y": 50.0, "shot_type": "2pt", "sub_type": "layup"}, high_end=True)
    assert near["distance_m"] == pytest.approx(0.0, abs=1e-6) and far["distance_m"] == pytest.approx(0.0, abs=1e-6)
    assert locate({"x": 6.0, "y": 50.0, "shot_type": "2pt", "sub_type": "tipinlayup"}, False)["location_imputed"]
    assert zone("3pt", 1.0, 0.5, 7.0) == "corner_3" and zone("3pt", 20.0, 7.5, 18.4) == "heave"
    assert zone("2pt", 4.0, 7.5, 2.4) == "paint" and zone("2pt", 6.5, 3.0, 6.5) == "midrange"


@needs_fixtures
def test_enrich_fixture_match(staged):
    e = enrich(staged.tables).tables
    checks = {c.name: c for c in V.run_enriched(staged.tables, e)}
    for name, c in checks.items():
        if c.blocking:
            assert c.ok, (name, c.failures)
    # 345960 has subs entered after a disruptive-foul trip but stamped with the score before it
    assert checks["stint_plus_minus"].ok, checks["stint_plus_minus"].failures
    p = e["possessions"]
    assert 65 <= p.filter(pl.col("team_season_id") == 266055).height <= 85
