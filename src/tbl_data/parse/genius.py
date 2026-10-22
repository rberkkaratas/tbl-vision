"""Genius LiveStats data.json -> row dicts. Pure: no IO, no TBF lookups.

Rows carry Genius-native keys (tno 1 = home, 2 = away; pno = per-match player number);
build.py maps them to TBF ids. Verified on 2026-27: tm "1" is always the TBF home team.
"""

from __future__ import annotations

from .common import clean, minutes_to_s, to_int
from .drift import Drift

# Genius player field -> staged column (schemas.BOX_STATS order)
BOX_PLAYER = {
    "sPoints": "pts", "sFieldGoalsMade": "fgm", "sFieldGoalsAttempted": "fga",
    "sTwoPointersMade": "fg2m", "sTwoPointersAttempted": "fg2a",
    "sThreePointersMade": "fg3m", "sThreePointersAttempted": "fg3a",
    "sFreeThrowsMade": "ftm", "sFreeThrowsAttempted": "fta",
    "sReboundsOffensive": "oreb", "sReboundsDefensive": "dreb", "sReboundsTotal": "reb",
    "sAssists": "ast", "sTurnovers": "tov", "sSteals": "stl", "sBlocks": "blk",
    "sBlocksReceived": "blk_against", "sFoulsPersonal": "pf", "sFoulsOn": "fouls_drawn",
    "sPlusMinusPoints": "plus_minus", "eff_1": "eff",
    "sPointsSecondChance": "pts_second_chance", "sPointsFastBreak": "pts_fast_break",
    "sPointsInThePaint": "pts_paint",
}
# Team totals include team rebounds/turnovers (not credited to a player).
BOX_TEAM = {"tot_" + k: v for k, v in BOX_PLAYER.items() if k != "sPlusMinusPoints"} | {
    "tot_sReboundsTeamOffensive": "team_reb_off", "tot_sReboundsTeamDefensive": "team_reb_def",
    "tot_sTurnoversTeam": "team_tov", "tot_sFoulsTeam": "team_fouls",
    "tot_sPointsFromTurnovers": "pts_off_tov", "tot_sBenchPoints": "pts_bench",
    "tot_sBiggestLead": "biggest_lead", "tot_sBiggestScoringRun": "biggest_run",
    "tot_sLeadChanges": "lead_changes", "tot_sTimesScoresLevel": "times_tied",
}


def periods(doc: dict) -> tuple[int, int, int]:
    """(regular periods, regular minutes, overtime minutes)."""
    return (
        int(doc.get("periodsMax") or 4),
        int(doc.get("periodLengthREGULAR") or doc.get("periodLength") or 10),
        int(doc.get("periodLengthOVERTIME") or 5),
    )


def period_ord(period: int, ptype: str, n_regular: int) -> int:
    return period if ptype == "REGULAR" else n_regular + period


def period_label(ordinal: int, n_regular: int) -> str:
    return f"Q{ordinal}" if ordinal <= n_regular else f"OT{ordinal - n_regular}"


def clock_s(clock: str) -> float:
    """'MM:SS:cc' (or 'MM:SS') time remaining -> seconds."""
    p = clock.split(":")
    s = int(p[0]) * 60 + int(p[1])
    return s + (int(p[2]) / 100 if len(p) > 2 else 0.0)


def match_info(doc: dict, drift: Drift, ctx: str) -> dict:
    drift.check("genius.root", doc, ctx)
    n_reg, reg_min, ot_min = periods(doc)
    ots = {e["period"] for e in doc.get("pbp") or [] if e.get("periodType") == "OVERTIME"}
    return {
        "attendance": to_int(doc.get("attendance")) or None,
        "period_minutes": reg_min,
        "overtime_minutes": ot_min,
        "n_overtimes": max(ots, default=0),
    }


def team_rows(doc: dict, match_id: int, drift: Drift) -> list[dict]:
    rows = []
    for tno, t in sorted(doc["tm"].items()):
        drift.check("genius.team", t, f"{match_id}/tm{tno}")
        r = {"match_id": match_id, "tno": int(tno), "coach": clean(t.get("coach")),
             "assistant_coach": clean(t.get("assistcoach1"))}
        r |= {col: to_int(t.get(k)) or 0 for k, col in BOX_TEAM.items()}
        r["seconds"] = minutes_to_s(t.get("tot_sMinutes"))
        r["plus_minus"] = None
        r["time_leading_s"] = round(float(t.get("tot_sTimeLeading") or 0) * 60, 1)
        rows.append(r)
    return rows


def player_rows(doc: dict, match_id: int, drift: Drift) -> list[dict]:
    rows = []
    for tno, t in sorted(doc["tm"].items()):
        for pno, p in (t.get("pl") or {}).items():
            drift.check("genius.player", p, f"{match_id}/tm{tno}/pno{pno}")
            r = {"match_id": match_id, "tno": int(tno), "pno": int(pno),
                 "jersey": str(p.get("shirtNumber", "")).strip(),
                 "name": clean(f"{p.get('firstName', '')} {p.get('familyName', '')}"),
                 "starter": bool(p.get("starter")), "captain": bool(p.get("captain"))}
            r |= {col: to_int(p.get(k)) or 0 for k, col in BOX_PLAYER.items()}
            r["plus_minus"] = to_int(p.get("sPlusMinusPoints"))
            r["seconds"] = minutes_to_s(p.get("sMinutes"))
            r["played"] = r["seconds"] > 0
            rows.append(r)
    return rows


def event_rows(doc: dict, match_id: int, drift: Drift) -> list[dict]:
    n_reg, reg_min, ot_min = periods(doc)
    rows = []
    for e in doc.get("pbp") or []:
        drift.check("genius.event", e, f"{match_id}/a{e.get('actionNumber')}")
        ordinal = period_ord(e["period"], e["periodType"], n_reg)
        cs = clock_s(e["clock"])
        start = min(ordinal - 1, n_reg) * reg_min * 60 + max(ordinal - 1 - n_reg, 0) * ot_min * 60
        length = (reg_min if ordinal <= n_reg else ot_min) * 60
        tno = e.get("tno") or 0
        rows.append({
            "match_id": match_id, "action_number": e["actionNumber"],
            "period_ord": ordinal, "period_label": period_label(ordinal, n_reg),
            "clock": e["clock"], "clock_s": cs, "elapsed_s": round(start + length - cs, 2),
            "tno": tno or None, "is_home": {1: True, 2: False}.get(tno),
            "pno": e.get("pno") or None,
            "action_type": e["actionType"], "sub_type": clean(e.get("subType")),
            "qualifiers": list(e.get("qualifier") or []),
            "success": bool(e.get("success")), "scoring": bool(e.get("scoring")),
            "score_home": to_int(e.get("s1")) or 0, "score_away": to_int(e.get("s2")) or 0,
            "previous_action": to_int(e.get("previousAction")),
        })
    rows.sort(key=lambda r: r["action_number"])  # feed is newest-first
    return rows


def shot_rows(doc: dict, match_id: int, drift: Drift) -> list[dict]:
    n_reg, _, _ = periods(doc)
    rows = []
    for tno, t in sorted(doc["tm"].items()):
        for s in t.get("shot") or []:
            drift.check("genius.shot", s, f"{match_id}/shot{s.get('actionNumber')}")
            rows.append({
                "match_id": match_id, "action_number": s["actionNumber"], "tno": int(tno),
                "is_home": tno == "1", "pno": s["pno"],
                "period_ord": period_ord(s["per"], s["perType"], n_reg),
                "x": float(s["x"]), "y": float(s["y"]), "made": bool(s["r"]),
                "shot_type": s["actionType"], "sub_type": clean(s.get("subType")),
                "previous_action": to_int(s.get("previousAction")),
            })
    return rows


SHOT_POINTS = {"2pt": 2, "3pt": 3, "freethrow": 1}


def period_rows(events: list[dict], n_regular: int) -> list[dict]:
    """Points per team per period, summed from made shots and free throws.

    The running score (s1/s2) is not used: late scorer corrections leave it stale on
    the last events of a period (seen in 345947).
    """
    pts: dict[tuple[int, int], int] = {}
    for e in events:
        pts.setdefault((1, e["period_ord"]), 0)
        pts.setdefault((2, e["period_ord"]), 0)
        if e["success"] and e["tno"] and e["action_type"] in SHOT_POINTS:
            pts[(e["tno"], e["period_ord"])] += SHOT_POINTS[e["action_type"]]
    return [{"tno": tno, "period_ord": p, "period_label": period_label(p, n_regular), "pts": n}
            for (tno, p), n in sorted(pts.items(), key=lambda kv: (kv[0][1], kv[0][0]))]
