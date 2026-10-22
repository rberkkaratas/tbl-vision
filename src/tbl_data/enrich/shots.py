"""Shot locations in metres plus context (assist, block, foul, possession, game state).

Genius x/y are 0-100 of the full 28 x 15 m court, not normalised to one basket: a team
attacks one end in the first half and the other in the second half (overtime keeps the
second-half end). We find each team's end per half from the majority of its shots, so a
few heaves from the far half do not flip it, then rotate shots at the x>50 end by 180°
(x' = 100-x, y' = 100-y) so every shot is measured from the attacked baseline.
FIBA geometry: basket centre 1.575 m from the baseline on the centre line, 3pt arc
6.75 m, straight corner segments 0.90 m from the sidelines up to 2.99 m from the baseline.
"""

from __future__ import annotations

import math
from collections import Counter

from .ft_trips import trip_after_shot

COURT_X, COURT_Y = 28.0, 15.0
RIM_X, RIM_Y = 1.575, 7.5
CORNER_X = 2.99  # where the corner 3pt straight line meets the arc
CORNER_Y = 0.90
PAINT_X, PAINT_HALF_W = 5.80, 2.45
RIM_R = 1.25
IMPUTED_SPOTS = {(6.0, 50.0), (94.0, 50.0)}  # Genius' default spot for tip-ins


def attack_ends(shots: list[dict]) -> dict[tuple[int, int], bool]:
    """(team, half) -> True if the team attacks the x>50 basket. half: 1 = Q1-Q2, 2 = Q3+."""
    votes: dict[tuple[int, int], Counter] = {}
    for s in shots:
        key = (s["team_season_id"], 1 if s["period_ord"] <= 2 else 2)
        votes.setdefault(key, Counter())[s["x"] > 50] += 1
    ends = {k: v.most_common(1)[0][0] for k, v in votes.items()}
    # a team with shots in only one half attacks the other end in the other half
    for (team, half), high in list(ends.items()):
        ends.setdefault((team, 3 - half), not high)
    return ends


def zone(shot_type: str, x_m: float, y_m: float, dist: float) -> str:
    if x_m > COURT_X / 2:
        return "heave"
    if shot_type == "3pt":
        return "corner_3" if x_m <= CORNER_X and (y_m < CORNER_Y or y_m > COURT_Y - CORNER_Y) else "above_break_3"
    if dist <= RIM_R:
        return "rim"
    if x_m <= PAINT_X and abs(y_m - RIM_Y) <= PAINT_HALF_W:
        return "paint"
    return "midrange"


def locate(s: dict, high_end: bool) -> dict:
    x, y = (100 - s["x"], 100 - s["y"]) if high_end else (s["x"], s["y"])
    x_m, y_m = x * COURT_X / 100, y * COURT_Y / 100
    dist = math.hypot(x_m - RIM_X, y_m - RIM_Y)
    return {
        "x_m": round(x_m, 3), "y_m": round(y_m, 3), "distance_m": round(dist, 3),
        "zone": zone(s["shot_type"], x_m, y_m, dist),
        "location_imputed": (s["x"], s["y"]) in IMPUTED_SPOTS and (s["sub_type"] or "").startswith("tipin"),
    }


def enrich_shots(shots: list[dict], events: list[dict], trips: list[dict], trip_of: dict[int, int],
                 possessions: list[dict]) -> list[dict]:
    """shots + events of one match (events in canonical order, enriched) -> shot rows."""
    ends = attack_ends(shots)
    by_an = {e["action_number"]: e for e in events}
    idx = {e["action_number"]: i for i, e in enumerate(events)}
    # score before each event, replayed in our order (Genius' stamps follow its own clock
    # order, which is off where we reordered)
    before: dict[int, tuple[int, int]] = {}
    home = away = 0
    for e in events:
        before[e["action_number"]] = (home, away)
        if e["action_type"] in ("2pt", "3pt", "freethrow") and e["success"] and e.get("team_season_id") is not None:
            pts = {"2pt": 2, "3pt": 3, "freethrow": 1}[e["action_type"]]
            if e["is_home"]:
                home += pts
            else:
                away += pts
    linked: dict[int, dict[str, dict]] = {}  # shot action -> {"assist": e, "block": e}
    for e in events:
        if e["action_type"] in ("assist", "block") and e.get("previous_action") is not None:
            linked.setdefault(e["previous_action"], {})[e["action_type"]] = e
    poss = {p["possession_no"]: p for p in possessions}
    out = []
    for s in shots:
        e = by_an.get(s["action_number"])
        r = dict(s) | locate(s, ends[(s["team_season_id"], 1 if s["period_ord"] <= 2 else 2)])
        q = set(e["qualifiers"]) if e else set()
        a, b = linked.get(s["action_number"], {}).get("assist"), linked.get(s["action_number"], {}).get("block")
        r |= {
            "seq": e["seq"] if e else None,
            "elapsed_s": e["elapsed_s"] if e else None,
            "clock_s": e["clock_s"] if e else None,
            "assisted": a is not None, "assist_player_id": a["player_id"] if a else None,
            "blocked": b is not None, "block_player_id": b["player_id"] if b else None,
            "second_chance": "2ndchance" in q, "fast_break": "fastbreak" in q,
            "from_turnover": "fromturnover" in q, "in_paint": "pointsinthepaint" in q,
            "fouled": _fouled(events, idx.get(s["action_number"]), trips, trip_of),
        }
        p = poss.get(e["possession_no"]) if e else None
        r["possession_no"] = p["possession_no"] if p else None
        r["possession_elapsed_s"] = round(e["elapsed_s"] - p["start_s"], 2) if p else None
        if e:
            h, a = before[s["action_number"]]
            r["margin_before"] = h - a if s["is_home"] else a - h
            r["offense_lineup"] = e["home_lineup"] if s["is_home"] else e["away_lineup"]
            r["defense_lineup"] = e["away_lineup"] if s["is_home"] else e["home_lineup"]
        else:
            r["margin_before"], r["offense_lineup"], r["defense_lineup"] = None, [], []
        out.append(r)
    return out


def _fouled(events: list[dict], i: int | None, trips: list[dict], trip_of: dict[int, int]) -> bool:
    """The shooter got a shooting-foul trip in the dead ball right after the shot."""
    if i is None:
        return False
    t = trip_after_shot(events, i, trips, trip_of)
    return t is not None and t["trip_type"] in ("and_one", "shooting_2pt", "shooting_3pt", "shooting")
