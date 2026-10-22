"""Split a game into possessions (FIBA rules, Oliver definition: an offensive rebound
continues the same possession).

A possession of team T ends after:
  made field goal (unless the shooter's 1-FT trip follows in the same dead ball: and-one)
  made last free throw of a trip           (missed last FT: the rebound decides)
  defensive rebound by the other team      turnover by T          end of period
Technical free throws never change possession. After unsportsmanlike-type fouls the
fouled team shoots and keeps the ball, so their trip does not end it (nor does another
trip shot in the same dead ball).

Who has the ball is otherwise inferred from offensive actions (shots, non-technical
free throws, turnovers, offensive rebounds). If the other team makes one without an end
event, the possession is switched: after a held ball that is the alternating arrow,
otherwise a gap in the feed, logged as an anomaly.

Every event belongs to the possession in which it happened; an action that describes
another one (assist, steal, block, foulon) belongs to the possession of its target.
"""

from __future__ import annotations

from .ft_trips import trip_after_shot

POINTS = {"2pt": 2, "3pt": 3, "freethrow": 1}
DESCRIBES = {"assist", "steal", "block", "foulon"}


def _offensive(e: dict, trip: dict | None) -> bool:
    a = e["action_type"]
    if a in ("2pt", "3pt"):
        return True
    if a == "freethrow":
        return not (trip and trip["trip_type"] == "technical")
    if a == "turnover":
        return e["team_season_id"] is not None
    return a == "rebound" and e["sub_type"] == "offensive"


def build_possessions(match_id: int, events: list[dict], trips: list[dict], trip_of: dict[int, int],
                      teams: tuple[int, int]) -> tuple[list[dict], list[dict]]:
    """events in canonical order (with lineups) -> (possession rows, anomaly rows).

    Sets e['possession_no'] and e['offense_team_id'] on every event.
    """
    trip_by_no = {t["trip_no"]: t for t in trips}
    other = {teams[0]: teams[1], teams[1]: teams[0]}
    shells: list[dict] = []  # possessions as opened; team may be unknown until the first offensive action
    anomalies: list[dict] = []
    cur: dict | None = None
    flexible = False  # after a jump ball the next offensive action decides, no anomaly

    def start(team, start_type, period_ord):
        nonlocal cur
        cur = {"team_season_id": team, "start_type": start_type, "end_type": None, "period_ord": period_ord,
               "offensive_actions": 0}
        shells.append(cur)

    def end(end_type, next_team, next_type, period_ord):
        cur["end_type"] = end_type
        start(next_team, next_type, period_ord)

    for i, e in enumerate(events):
        a, team = e["action_type"], e.get("team_season_id")
        trip = trip_by_no.get(trip_of.get(e["action_number"])) if a == "freethrow" else None

        if (a == "period" and e["sub_type"] == "start") or cur is None:
            if cur is None or cur["offensive_actions"] or cur["period_ord"] != e["period_ord"]:
                start(None, "period_start", e["period_ord"])
            flexible = True

        if team is not None and _offensive(e, trip):
            if cur["team_season_id"] is None:
                cur["team_season_id"] = team
            elif cur["team_season_id"] != team:
                retained = trip is not None and trip["trip_type"] == "unsportsmanlike"
                if retained and not flexible and cur["offensive_actions"] == 0:
                    # fouled before the other team got going (e.g. right after a basket): their ball
                    cur["team_season_id"] = team
                    cur["start_type"] = "unsportsmanlike_foul"
                else:
                    kind = "jumpball" if flexible else ("unsportsmanlike_foul" if retained else "inferred")
                    if kind == "inferred":
                        anomalies.append({"match_id": match_id, "action_number": e["action_number"], "seq": e["seq"],
                                          "team_season_id": team, "player_id": e.get("player_id"),
                                          "issue": "possession_switch_without_end",
                                          "period_label": e["period_label"], "clock": e["clock"]})
                    end(kind, team, kind, e["period_ord"])
            cur["offensive_actions"] += 1
            flexible = False

        e["_shell"] = cur
        off = cur["team_season_id"]

        # does the ball change hands after this event?
        if a == "jumpball" and e["sub_type"] in ("heldball", "lodgedball", "outofbounds"):
            flexible = True
        if a == "period" and e["sub_type"] == "end":
            cur["end_type"] = "period_end"
            cur = None
            continue
        if off is None or team is None:
            continue
        if a in ("2pt", "3pt") and e["success"] and team == off:
            t = trip_after_shot(events, i, trips, trip_of)
            if not (t and t["n_awarded"] == 1 and t["trip_type"] in ("and_one", "unsportsmanlike")):
                end("made_fg", other[off], "after_made_fg", e["period_ord"])
        elif a == "freethrow" and team == off and trip and e["seq"] == trip["last_seq"]:
            if trip["trip_type"] not in ("technical", "unsportsmanlike") and e["success"] \
                    and not _ball_retained_same_dead_ball(trip, trips):
                end("made_ft", other[off], "after_made_ft", e["period_ord"])
        elif a == "rebound" and e["sub_type"] == "defensive" and team != off:
            end("defensive_rebound", team, "after_defensive_rebound", e["period_ord"])
        elif a == "turnover" and team == off:
            end("turnover", other[off], "after_steal" if _steal_follows(events, i) else "after_turnover",
                e["period_ord"])

    return _finalize(match_id, events, shells, trip_by_no, trip_of), anomalies


def _finalize(match_id, events, shells, trip_by_no, trip_of) -> list[dict]:
    """Number the possessions that have a team, attach every event, count from the events."""
    # events in a shell without a team (e.g. dead-ball actions before a period's first
    # offensive action) belong to the next real possession, or the last one at the end
    nxt = None
    for e in reversed(events):
        s = e.pop("_shell")
        if s["team_season_id"] is not None:
            nxt = s
        e["_p"] = s if s["team_season_id"] is not None else nxt
    prev = None
    for e in events:
        if e["_p"] is None:
            e["_p"] = prev
        prev = e["_p"]
    by_an = {e["action_number"]: e for e in events}
    for e in events:
        t = by_an.get(e.get("previous_action")) if e["action_type"] in DESCRIBES else None
        if t is not None and t["period_ord"] == e["period_ord"] and t["_p"] is not None:
            e["_p"] = t["_p"]
    # a possession opened by the very last event of a period/game has no events: drop it
    used = {id(e["_p"]) for e in events if e["_p"] is not None}
    kept = [s for s in shells if id(s) in used]
    for k, s in enumerate(kept, 1):
        s["no"] = k

    rows = {id(s): {"match_id": match_id, "possession_no": s["no"], "team_season_id": s["team_season_id"],
                    "period_ord": s["period_ord"], "start_type": s["start_type"],
                    "end_type": s["end_type"] or "game_end", "start_seq": None, "end_seq": None,
                    "start_s": None, "end_s": None, "pts": 0, "pts_against": 0,
                    "fga": 0, "fg3a": 0, "fta": 0, "oreb": 0, "tov": 0} for s in kept}
    last_end = None
    for e in events:
        s = e.pop("_p")
        if s is None:
            e["possession_no"] = e["offense_team_id"] = None
            continue
        r = rows[id(s)]
        e["possession_no"], e["offense_team_id"] = r["possession_no"], r["team_season_id"]
        if r["start_seq"] is None:  # events come in order; only a describing action can trail
            # the ball changed hands at the previous possession's last event
            r["start_seq"] = e["seq"]
            r["start_s"] = last_end if last_end is not None and last_end[0] == e["period_ord"] else None
            r["start_s"] = r["start_s"][1] if r["start_s"] else e["elapsed_s"]
        if r["end_seq"] is None or e["seq"] > r["end_seq"]:
            r["end_seq"], r["end_s"] = e["seq"], e["elapsed_s"]
        last_end = (e["period_ord"], e["elapsed_s"])
        a, team, off = e["action_type"], e.get("team_season_id"), r["team_season_id"]
        trip = trip_by_no.get(trip_of.get(e["action_number"])) if a == "freethrow" else None
        if a in POINTS and team is not None:
            if e["success"]:
                r["pts" if team == off else "pts_against"] += POINTS[a]
            if team == off and a in ("2pt", "3pt"):
                r["fga"] += 1
                r["fg3a"] += a == "3pt"
            elif team == off and not (trip and trip["trip_type"] == "technical"):
                r["fta"] += 1
        if a == "rebound" and e["sub_type"] == "offensive" and team == off:
            r["oreb"] += 1
        if a == "turnover" and team == off:
            r["tov"] += 1
    out = list(rows.values())
    for r in out:
        r["duration_s"] = round(r["end_s"] - r["start_s"], 2)
    return out


def _ball_retained_same_dead_ball(trip: dict, trips: list[dict]) -> bool:
    """Another trip of the same team at the same moment was for a ball-retaining foul."""
    return any(t["trip_type"] == "unsportsmanlike" and t["team_season_id"] == trip["team_season_id"]
               and t["period_ord"] == trip["period_ord"] and t["elapsed_s"] == trip["elapsed_s"]
               and t is not trip for t in trips)


def _steal_follows(events: list[dict], i: int) -> bool:
    tov = events[i]
    for e in events[i + 1: i + 4]:
        if e["action_type"] == "steal" and e.get("previous_action") == tov["action_number"]:
            return True
    return False
