"""Canonical event order.

Genius action numbers follow data entry, and ~0.3% of actions are entered late, so
sorting by action_number alone can run the clock backwards. We sort by game time; inside
one clock second the score Genius stamped on each action (score_home + score_away, which
includes the action's own points) comes first, then entry order. The stamp shows where a
late entry belongs (a substitution stamped 34-50 came before the free throw that made it
36-50), and it is what Genius' own +/- follows. Then:
  - within one clock second, an action that references another (rebound -> shot,
    assist -> shot, steal -> turnover, foulon -> foul) is forced to come after its target;
  - a rebound whose clock was mistyped (often 00:00 at the buzzer) and that lands after
    plays entered later than itself is moved back right after its shot;
  - a shot (with its rebound) that lands between two free throws of one trip in the same
    second is moved before the trip's foul: a live ball cannot happen in between.
"""

from __future__ import annotations

import re

PLAYS = {"2pt", "3pt", "freethrow", "rebound", "turnover", "jumpball"}


def order_events(events: list[dict]) -> tuple[list[dict], list[dict]]:
    """events of one match -> (events in canonical order with 'seq', moved rebounds)."""
    out = sorted(events, key=lambda e: (e["period_ord"], e["elapsed_s"],
                                        (e["score_home"] or 0) + (e["score_away"] or 0), e["action_number"]))
    for _ in range(len(out)):  # one move per pass; moves are rare
        pos = {e["action_number"]: i for i, e in enumerate(out)}
        moved = False
        for i, e in enumerate(out):
            tgt = e.get("previous_action")
            j = pos.get(tgt) if tgt is not None else None
            if j is None or j < i:
                continue
            t = out[j]
            if (t["period_ord"], t["elapsed_s"]) != (e["period_ord"], e["elapsed_s"]):
                continue  # cross-second link: handled below for rebounds, else left as is
            out.insert(i, out.pop(j))
            moved = True
            break
        if not moved:
            break
    relinked = _relink_rebounds(out) + _shots_out_of_ft_trips(out)
    for k, e in enumerate(out, 1):
        e["seq"] = k
    return out, relinked


def _relink_rebounds(out: list[dict]) -> list[dict]:
    log = []
    for _ in range(len(out)):
        pos = {e["action_number"]: i for i, e in enumerate(out)}
        for i, e in enumerate(out):
            j = pos.get(e.get("previous_action")) if e["action_type"] == "rebound" else None
            if j is None or j >= i or out[j]["period_ord"] != e["period_ord"]:
                continue
            between = out[j + 1: i]
            if not any(b["action_type"] in PLAYS and b["action_number"] > e["action_number"] for b in between):
                continue
            k = j + 1  # after the shot and anything else hanging on it (a block)
            while k < i and out[k].get("previous_action") == e["previous_action"]:
                k += 1
            out.pop(i)
            nxt = out[k]
            new_t = max(out[k - 1]["elapsed_s"], min(e["elapsed_s"], nxt["elapsed_s"]))
            log.append({"action_number": e["action_number"], "team_season_id": e.get("team_season_id"),
                        "player_id": e.get("player_id"), "period_label": e["period_label"], "clock": e["clock"],
                        "note": f"moved after {e['previous_action']}"})
            _retime(e, new_t)
            out.insert(k, e)
            break
        else:
            break
    return log


_KOFN = re.compile(r"(\d)of(\d)")


def _shots_out_of_ft_trips(out: list[dict]) -> list[dict]:
    log = []
    i = 0
    while i < len(out):
        e = out[i]
        m = _KOFN.fullmatch(e["sub_type"] or "") if e["action_type"] == "freethrow" else None
        if not (m and m.group(1) == "1" and m.group(2) != "1"):
            i += 1
            continue
        same = lambda x: (x["period_ord"], x["elapsed_s"]) == (e["period_ord"], e["elapsed_s"])  # noqa: E731
        last = next((j for j in range(i + 1, len(out)) if not same(out[j]) or (
            out[j]["action_type"] == "freethrow" and out[j]["player_id"] == e["player_id"]
            and out[j]["sub_type"] == f"{m.group(2)}of{m.group(2)}")), None)
        if last is None or not same(out[last]):
            i += 1
            continue
        shots = {x["action_number"] for x in out[i + 1: last] if x["action_type"] in ("2pt", "3pt")}
        live = [x for x in out[i + 1: last] if x["action_number"] in shots or x.get("previous_action") in shots]
        if not live:
            i += 1
            continue
        f = next((j for j in range(i - 1, -1, -1) if not same(out[j]) or (
            out[j]["action_type"] == "foul" and f"{m.group(2)}freethrow" in out[j]["qualifiers"])), i)
        f = f if same(out[f]) else i
        for x in live:
            out.remove(x)
            log.append({"action_number": x["action_number"], "team_season_id": x.get("team_season_id"),
                        "player_id": x.get("player_id"), "period_label": x["period_label"], "clock": x["clock"],
                        "note": f"moved before free-throw trip of action {e['action_number']}"})
        out[f:f] = live
        i += len(live) + 1
    return log


def _retime(e: dict, t: float) -> None:
    """Move an event to game time t (clock_s counts down, so it moves the other way)."""
    if e.get("clock_s") is not None:
        e["clock_s"] = round(e["clock_s"] - (t - e["elapsed_s"]), 2)
    e["elapsed_s"] = t


def apply_overrides(events: list[dict], overrides: dict) -> tuple[list[dict], list[dict]]:
    """Manual fixes from overrides/{match_id}.json, applied after canonical ordering.

    {"reorder": [{"actions": [957, 958], "after": 956, "reason": "..."}]} moves the listed
    actions (keeping their order) right after `after` and gives them its game time.
    Returns (events re-sequenced, log rows).
    """
    log = []
    for fix in overrides.get("reorder", []):
        moving = [e for e in events if e["action_number"] in set(fix["actions"])]
        rest = [e for e in events if e["action_number"] not in set(fix["actions"])]
        k = next((i for i, e in enumerate(rest) if e["action_number"] == fix["after"]), None)
        if k is None or len(moving) != len(fix["actions"]):
            raise ValueError(f"override {fix} refers to actions that are not in the match")
        anchor = rest[k]
        for e in moving:
            _retime(e, anchor["elapsed_s"])
            e["period_ord"], e["period_label"] = anchor["period_ord"], anchor["period_label"]
        events = rest[: k + 1] + moving + rest[k + 1:]
        log.append({"fix": "reorder", "actions": fix["actions"], "after": fix["after"], "reason": fix.get("reason")})
    for k, e in enumerate(events, 1):
        e["seq"] = k
    return events, log
