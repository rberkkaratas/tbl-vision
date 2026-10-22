"""Canonical event order.

Genius action numbers follow data entry, and ~0.3% of actions are entered late, so
sorting by action_number alone can run the clock backwards. We sort by game time and
use action_number only to break ties inside the same clock second (entry order is
right there in all but a few cases). Then, within one clock second, an action that
references another (rebound -> shot, assist -> shot, steal -> turnover, foulon -> foul)
is forced to come after its target.
"""

from __future__ import annotations


def order_events(events: list[dict]) -> tuple[list[dict], int]:
    """events of one match -> (events in canonical order with 'seq', number of moves)."""
    out = sorted(events, key=lambda e: (e["period_ord"], e["elapsed_s"], e["action_number"]))
    moves = 0
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
                continue  # cross-second link: a clock entry error, left as is
            out.insert(i, out.pop(j))
            moves += 1
            moved = True
            break
        if not moved:
            break
    for k, e in enumerate(out, 1):
        e["seq"] = k
    return out, moves


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
        k = next(i for i, e in enumerate(rest) if e["action_number"] == fix["after"])
        anchor = rest[k]
        for e in moving:
            e["elapsed_s"], e["period_ord"] = anchor["elapsed_s"], anchor["period_ord"]
        events = rest[: k + 1] + moving + rest[k + 1:]
        log.append({"fix": "reorder", "actions": fix["actions"], "after": fix["after"], "reason": fix.get("reason")})
    for k, e in enumerate(events, 1):
        e["seq"] = k
    return events, log
