"""Who is on court: replay substitutions from the starting fives.

Each event gets both teams' on-court players. Stints are the intervals in which a
team's five did not change (split at period ends). Problems are recorded, never
silently fixed: a player acting while not on court, a substitution that does not fit
the current five, or a five that is not five players after a substitution window.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SHOTS = {"2pt": 2, "3pt": 3, "freethrow": 1}
# Actions only a player on court can make. Technical/disqualifying/disruptive fouls can
# be called on the bench, so they are not checked.
ON_COURT_ACTIONS = {"2pt", "3pt", "freethrow", "rebound", "assist", "steal", "block", "turnover", "foulon"}
ON_COURT_FOULS = {"personal", "offensive", "unsportsmanlike"}


@dataclass
class LineupResult:
    events: list[dict]
    stints: list[dict]
    issues: list[dict]
    player_seconds: dict[tuple[int, int], float] = field(default_factory=dict)  # (team, player) -> s
    player_pm: dict[tuple[int, int], int] = field(default_factory=dict)  # (team, player) -> +/-


def track(match_id: int, events: list[dict], starters: dict[int, set[int]]) -> LineupResult:
    """events: one match in canonical order. starters: team_season_id -> 5 player ids."""
    teams = list(starters)
    ft_actions = {e["action_number"] for e in events if e["action_type"] == "freethrow"}
    on = {t: set(p) for t, p in starters.items()}
    issues: list[dict] = []
    stints: list[dict] = []
    seconds: dict[tuple[int, int], float] = {}
    pm: dict[tuple[int, int], int] = {}
    open_stint: dict[int, dict] = {}
    pending_check = False

    def issue(e, team, player, kind):
        issues.append({"match_id": match_id, "action_number": e["action_number"], "seq": e["seq"],
                       "team_season_id": team, "player_id": player, "issue": kind,
                       "period_label": e["period_label"], "clock": e["clock"]})

    def close(team, t_end):
        st = open_stint.pop(team, None)
        if st is None:
            return
        st["end_s"] = t_end
        st["seconds"] = round(t_end - st["start_s"], 2)
        for p in st["lineup"]:
            seconds[(team, p)] = seconds.get((team, p), 0.0) + st["seconds"]
        if st["seconds"] > 0 or st["pts_for"] or st["pts_against"]:
            stints.append(st)

    def open_(team, e):
        open_stint[team] = {"match_id": match_id, "team_season_id": team, "period_ord": e["period_ord"],
                            "start_s": e["elapsed_s"], "start_seq": e["seq"], "lineup": sorted(on[team]),
                            "pts_for": 0, "pts_against": 0}

    for i, e in enumerate(events):
        team, player, a = e.get("team_season_id"), e.get("player_id"), e["action_type"]
        if a == "period" and e["sub_type"] == "start":
            for t in teams:
                if t in open_stint and open_stint[t]["lineup"] != sorted(on[t]):
                    close(t, e["elapsed_s"])
                if t not in open_stint:
                    open_(t, e)
        elif a == "substitution" and team in on:
            if e["sub_type"] == "out":
                if player not in on[team]:
                    issue(e, team, player, "sub_out_not_on_court")
                on[team].discard(player)
            else:
                if player in on[team]:
                    issue(e, team, player, "sub_in_already_on")
                on[team].add(player)
            pending_check = True
        elif player and team in on and player not in on[team] and (
                a in ON_COURT_ACTIONS or (a == "foul" and e["sub_type"] in ON_COURT_FOULS)) and not (
                a == "assist" and e.get("previous_action") in ft_actions):  # pass came before the foul
            issue(e, team, player, "acting_off_court")

        if a in SHOTS and e["success"] and team in on:
            pts = SHOTS[a]
            for t in teams:  # points belong to the five on court now, even mid-window
                if t in open_stint and open_stint[t]["lineup"] != sorted(on[t]):
                    close(t, e["elapsed_s"])
                if t not in open_stint:  # e.g. a technical FT logged before the period-start event
                    open_(t, e)
            for t in teams:
                sign = 1 if t == team else -1
                for p in on[t]:
                    pm[(t, p)] = pm.get((t, p), 0) + sign * pts
                if t in open_stint:
                    open_stint[t]["pts_for" if t == team else "pts_against"] += pts

        home, away = teams
        e["home_lineup"] = sorted(on[home])
        e["away_lineup"] = sorted(on[away])
        # a substitution window is over when the clock moves on (other dead-ball actions
        # can be logged between the "in" and the "out" of one change)
        nxt = events[i + 1] if i + 1 < len(events) else None
        if pending_check and not (nxt and (nxt["period_ord"], nxt["elapsed_s"]) == (e["period_ord"], e["elapsed_s"])):
            pending_check = False
            for t in teams:
                if len(on[t]) != 5:
                    issue(e, t, None, f"lineup_size_{len(on[t])}")
                if t in open_stint and open_stint[t]["lineup"] != sorted(on[t]):
                    close(t, e["elapsed_s"])
                    open_(t, e)
        if a == "period" and e["sub_type"] == "end":
            for t in teams:
                close(t, e["elapsed_s"])

    for t in list(open_stint):  # feeds that end without a period-end event
        close(t, events[-1]["elapsed_s"])
    for k, st in enumerate(stints, 1):
        st["stint_no"] = k
    return LineupResult(events, stints, issues, seconds, pm)
