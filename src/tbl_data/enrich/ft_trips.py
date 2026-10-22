"""Free-throw trips: group 1ofN..NofN attempts and attach the foul that caused them.

Genius does not link free throws to fouls, so a trip is matched to the most recent
unused foul by the other team (or a coach/bench technical) that awarded N shots
(qualifier '<N>freethrow').
"""

from __future__ import annotations

import re

TECHNICAL = {"technical", "coachTechnical", "benchTechnical"}
# After these the fouled team shoots and then keeps the ball (FIBA throw-in)
BALL_RETAINED = {"unsportsmanlike", "flagrant", "disruptive", "disqualifying", "coachDisqualifying"}
_KOFN = re.compile(r"(\d)of(\d)")


def trip_type(foul: dict | None, n: int) -> str:
    if foul is None:
        return "unknown"
    st, q = foul["sub_type"], foul["qualifiers"]
    if st in TECHNICAL:
        return "technical"
    if st in BALL_RETAINED:
        return "unsportsmanlike"
    if "shooting" in q:
        return {1: "and_one", 2: "shooting_2pt", 3: "shooting_3pt"}.get(n, "shooting")
    return "penalty"  # non-shooting foul with the team in the bonus


def build_trips(match_id: int, events: list[dict]) -> tuple[list[dict], dict[int, int]]:
    """events in canonical order -> (trip rows, {ft action_number: trip_no})."""
    trips: list[dict] = []
    trip_of: dict[int, int] = {}
    used: set[int] = set()
    open_trips: dict[tuple, dict] = {}  # (shooter, n) -> unfinished trip; a technical FT can interleave
    for i, e in enumerate(events):
        if e["action_type"] != "freethrow":
            continue
        m = _KOFN.fullmatch(e["sub_type"] or "")
        k, n = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
        cur = open_trips.get((e["player_id"], n))
        if not (cur and k > 1 and k == cur["attempts"] + 1):
            foul = _find_foul(events, i, e, n, used)
            if foul:
                used.add(foul["action_number"])
            cur = {
                "match_id": match_id, "trip_no": len(trips) + 1, "team_season_id": e["team_season_id"],
                "shooter_id": e["player_id"], "n_awarded": n, "attempts": 0, "made": 0,
                "foul_action": foul["action_number"] if foul else None,
                "foul_sub_type": foul["sub_type"] if foul else None,
                "fouler_id": foul["player_id"] if foul else None,
                "trip_type": trip_type(foul, n), "period_ord": e["period_ord"], "elapsed_s": e["elapsed_s"],
                "first_seq": e["seq"],
            }
            trips.append(cur)
            open_trips[(e["player_id"], n)] = cur
        cur["attempts"] += 1
        cur["made"] += int(e["success"])
        cur["last_seq"] = e["seq"]
        trip_of[e["action_number"]] = cur["trip_no"]
    for t in trips:
        t["complete"] = t["attempts"] == t["n_awarded"]
    return trips, trip_of


def _find_foul(events: list[dict], i: int, ft: dict, n: int, used: set[int]) -> dict | None:
    for j in range(i - 1, -1, -1):
        f = events[j]
        if f["period_ord"] != ft["period_ord"]:
            break
        if f["action_type"] != "foul" or f["action_number"] in used:
            continue
        if f"{n}freethrow" not in f["qualifiers"]:
            continue
        if f["team_season_id"] == ft["team_season_id"] and f["sub_type"] not in TECHNICAL:
            continue  # a player foul awards shots to the other team
        return f
    return None
