"""TBF API responses -> row dicts. Pure: no IO.

Field quirks handled here (verified against Genius on 2026-27):
  twoPointShots / fieldGoalsMade (career)  = 2P made, not FG made
  freeThrows                               = FT made
  rivalFoul / yapilanFaul                  = fouls drawn
  yenilenBlok                              = blocks received
  freeThowsMade, tournovers, fauls         = misspelt FT made, turnovers, fouls
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from .common import clean, minutes_to_s, to_int
from .drift import Drift

TR = ZoneInfo("Europe/Istanbul")

BOX_PLAYER = {
    "points": "pts", "successfulShots": "fgm", "shotAttempts": "fga",
    "twoPointShots": "fg2m", "twoPointShotAttempts": "fg2a",
    "threePointShots": "fg3m", "threePointShotAttempts": "fg3a",
    "freeThrows": "ftm", "freeThrowAttempts": "fta",
    "offensiveRebounds": "oreb", "defensiveRebounds": "dreb", "totalRebounds": "reb",
    "assists": "ast", "turnovers": "tov", "steals": "stl", "blocks": "blk",
    "blocksReceived": "blk_against", "fouls": "pf", "rivalFoul": "fouls_drawn",
    "efficiency": "eff",
}
BOX_LISTS = {"homeTopFive": True, "homeBenchPlayers": True, "awayTopFive": False, "awayBenchPlayers": False}

CAREER = {
    "matchCount": "games", "totalPoints": "pts",
    "fieldGoalsMade": "fg2m", "fieldGoalsAttempted": "fg2a",
    "threePointsMade": "fg3m", "threePointsAttempted": "fg3a",
    "freeThowsMade": "ftm", "freeThowsAttempted": "fta",
    "offensiveRebounds": "oreb", "defensiveRebounds": "dreb", "rebounds": "reb",
    "assists": "ast", "steals": "stl", "tournovers": "tov", "blocks": "blk",
    "yenilenBlok": "blk_against", "fauls": "pf", "yapilanFaul": "fouls_drawn",
    "playerEfficiency": "eff",
}


def local_to_utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TR).astimezone(UTC)


def to_date(s: str | None) -> date | None:
    return datetime.fromisoformat(s).date() if s else None


def data(doc: dict | None):
    return (doc or {}).get("data")


def fixture_rows(doc: dict, week: int, phase: str | None, drift: Drift) -> list[dict]:
    rows = []
    for m in data(doc) or []:
        drift.check("tbf.fixture", m, f"fixture/{m.get('matchId')}")
        h, a = m["homeTeam"], m["awayTeam"]
        rows.append({
            "match_id": m["matchId"], "genius_id": to_int(m.get("genuisId")),  # (sic)
            "week": week, "phase": phase or "regular", "start_utc": local_to_utc(m["matchDate"]),
            "venue": clean(m.get("salonAdi")), "city": clean(m.get("il")),
            "home_team_id": h["id"], "away_team_id": a["id"],
            "home_score": to_int(h.get("score")), "away_score": to_int(a.get("score")),
            "youtube_url": clean(m.get("youtubeLink")), "broadcast": clean(m.get("broadcastChannel")),
            "match_code": clean(m.get("matchCode")),
        })
    return rows


def header_row(doc: dict, drift: Drift) -> dict:
    d = data(doc)
    drift.check("tbf.header", d, f"header/{d.get('genuisId')}")
    for side in ("homeTeam", "awayTeam"):
        drift.check("tbf.header_team", d[side], "header/team")
    return {
        "home_team_id": d["homeTeamProcessId"], "away_team_id": d["awayTeamProcessId"],
        "home_score": to_int(d.get("homeTeamScore")), "away_score": to_int(d.get("awayTeamScore")),
        "venue": clean(d.get("location")), "attendance": to_int(d.get("matchSpectatorCount")),
        "referee_1": clean(d.get("mainReferee")), "referee_2": clean(d.get("firstAssistantReferee")),
        "referee_3": clean(d.get("secondAssistantReferee")), "commissioner": clean(d.get("temsilciAdi")),
        "youtube_url": clean(d.get("liveVideoLink")), "broadcast": clean(d.get("matchBroadcastChannel")),
        "home_coach": clean(d["homeTeam"].get("coachName")), "away_coach": clean(d["awayTeam"].get("coachName")),
        "colors": {d["homeTeamProcessId"]: (d["homeTeam"].get("hexCode1"), d["homeTeam"].get("hexCode2")),
                   d["awayTeamProcessId"]: (d["awayTeam"].get("hexCode1"), d["awayTeam"].get("hexCode2"))},
    }


def box_rows(doc: dict, match_id: int, home_id: int, away_id: int, drift: Drift) -> list[dict]:
    d = data(doc)
    drift.check("tbf.box", d, f"{match_id}/box")
    rows = []
    for key, is_home in BOX_LISTS.items():
        for p in d.get(key) or []:
            drift.check("tbf.box_player", p, f"{match_id}/box/{p.get('playerId')}")
            r = {"match_id": match_id, "team_season_id": home_id if is_home else away_id,
                 "player_id": p["playerId"], "jersey": str(p.get("jerseyNumber", "")).strip(),
                 "name": clean(p.get("playerName")), "starter": bool(p.get("isStarter"))}
            r |= {col: to_int(p.get(k)) or 0 for k, col in BOX_PLAYER.items()}
            r["plus_minus"] = to_int(p.get("plusMinus"))
            r["seconds"] = minutes_to_s(p.get("minutesPlayed"))
            rows.append(r)
    return rows


def gameflow_rows(doc: dict, match_id: int, drift: Drift) -> list[dict]:
    rows = []
    for e in (data(doc) or {}).get("events") or []:
        drift.check("tbf.gameflow_event", e, f"{match_id}/flow{e.get('actionNumber')}")
        rows.append({
            "match_id": match_id, "action_number": e["actionNumber"], "period": e["quarterNumber"],
            "period_type": e["periodType"], "clock": e.get("time") or "", "score": clean(e.get("score")),
            "text": e.get("action") or "", "is_home": e.get("isHome"), "player_id": e.get("playerId") or None,
        })
    return rows


def shotchart_rows(doc: dict, match_id: int, drift: Drift) -> list[dict]:
    rows = []
    for s in (data(doc) or {}).get("shotInfos") or []:
        drift.check("tbf.shot", s, f"{match_id}/shotchart")
        rows.append({
            "match_id": match_id, "period": s["period"], "period_type": s["periodType"],
            "x": s["x"], "y": s["y"], "made": bool(s["isSucceed"]), "is_home": bool(s["isHome"]),
            "player_id": s["playerId"],
        })
    return rows


def team_rows(doc: dict, drift: Drift) -> list[dict]:
    rows = []
    for t in data(doc) or []:
        drift.check("tbf.team", t, f"team/{t.get('teamProcessId')}")
        rows.append({
            "team_season_id": t["teamProcessId"], "club_id": t["teamId"], "name": clean(t["teamName"]),
            "code": clean(t.get("teamCode")), "city": clean(t.get("teamCityName")),
            "logo_url": clean(t.get("teamLogoUrl")),
            "color_1": clean(t.get("hexRenkKodu1")), "color_2": clean(t.get("hexRenkKodu2")),
        })
    return rows


def player_list_rows(doc: dict, drift: Drift) -> list[dict]:
    rows = []
    for p in data(doc) or []:
        drift.check("tbf.player_list", p, f"player_list/{p.get('kisiID')}")
        rows.append({
            "player_id": p["kisiID"], "full_name": clean(p.get("adiSoyadi")),
            "birth_date": to_date(p.get("dogumTarihi")), "nationality": clean(p.get("tabiyet")),
            "nationality_code": clean(p.get("tabiyetKısa")), "team_season_id": p.get("takimID"),
            "jersey": clean(p.get("formaNo")), "photo_url": clean(p.get("fotograf")),
        })
    return rows


def profile_row(doc: dict, player_id: int, drift: Drift) -> dict:
    p = data(doc)["playerProfile"]
    drift.check("tbf.profile", p, f"profile/{player_id}")
    return {
        "player_id": player_id, "full_name": clean(p.get("fullName")), "birth_date": to_date(p.get("birthDate")),
        "nationality": clean(p.get("country")), "height_cm": to_int(p.get("height")),
        "weight_kg": to_int(p.get("weight")), "jersey": clean(p.get("formaNo")),
        "photo_url": clean(p.get("playerPic")),
    }


def career_rows(doc: dict, player_id: int, drift: Drift) -> list[dict]:
    rows = []
    for c in data(doc).get("careerInfo") or []:
        drift.check("tbf.career", c, f"career/{player_id}")
        r = {"player_id": player_id, "career_season": c["seasonCode"], "team_name": clean(c.get("teamName")),
             "competition": clean(c.get("activityName")), "competition_area": clean(c.get("faaliyetAlani")),
             "registered_at": c.get("processDate"),
             "seconds": minutes_to_s(c.get("formattedTotalSeconds"))}
        r |= {col: to_int(c.get(k)) or 0 for k, col in CAREER.items()}
        rows.append(r)
    return rows
