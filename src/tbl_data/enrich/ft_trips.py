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
    for i, e in enumerate(events):
        if e["action_type"] != "freethrow":
            continue
        m = _KOFN.fullmatch(e["sub_type"] or "")
        k, n = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
        cur = _continued(trips, e, k, n) if k > 1 else None
        if cur is None:
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
        cur["attempts"] += 1
        cur["made"] += int(e["success"])
        cur["last_seq"] = e["seq"]
        trip_of[e["action_number"]] = cur["trip_no"]
    for t in trips:
        t["complete"] = t["attempts"] == t["n_awarded"]
    return trips, trip_of


def _continued(trips: list[dict], ft: dict, k: int, n: int) -> dict | None:
    """The unfinished trip that attempt k of n continues: the shooter's own (other trips can
    interleave, e.g. a technical, or two fouls in one dead ball), else one a substitute finishes
    in the same dead ball after the fouled player left (injury, disqualification)."""
    cand = [t for t in reversed(trips) if t["team_season_id"] == ft["team_season_id"] and t["n_awarded"] == n
            and t["attempts"] == k - 1 and t["period_ord"] == ft["period_ord"]]
    own = [t for t in cand if t["shooter_id"] == ft["player_id"]]
    sub = [t for t in cand if t["elapsed_s"] == ft["elapsed_s"]]
    return (own or sub or [None])[0]


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


# a live-ball action: once one happens, a later free throw can no longer be for this shot
_LIVE = {"2pt", "3pt", "rebound", "turnover", "jumpball", "period"}
DEAD_BALL_LAG_S = 5  # longest seen between a basket and its late-entered and-one foul: 4 s


def trip_after_shot(events: list[dict], i: int, trips: list[dict], trip_of: dict[int, int]) -> dict | None:
    """The shooter's own free-throw trip in the dead ball that follows shot events[i], if any.

    Matched by order, not by exact clock: the foul and free throws are sometimes entered
    up to a few seconds later, after substitutions or a timeout. Technical free throws in
    between are skipped; any other trip, or a live-ball action, ends the dead ball.
    """
    shot = events[i]
    by_no = {t["trip_no"]: t for t in trips}
    for e in events[i + 1:]:
        if e["action_type"] in _LIVE or e["period_ord"] != shot["period_ord"] \
                or e["elapsed_s"] - shot["elapsed_s"] > DEAD_BALL_LAG_S:
            return None
        if e["action_type"] == "freethrow":
            t = by_no.get(trip_of.get(e["action_number"]))
            if t is None or t["trip_type"] == "technical":
                continue
            mine = t["shooter_id"] == shot["player_id"] or (
                # a 1-FT shooting foul only follows a basket: the feed credited another player
                t["trip_type"] == "and_one" and t["team_season_id"] == shot["team_season_id"])
            return t if mine else None
    return None
