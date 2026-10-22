"""staged -> enriched: canonical order, lineups/stints, free-throw trips, possessions, shots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import structlog

from .. import schemas
from .ft_trips import build_trips
from .lineups import track
from .order import apply_overrides, order_events
from .possessions import build_possessions
from .shots import enrich_shots

log = structlog.get_logger()

EVENT_EXTRA = ("seq", "possession_no", "offense_team_id", "home_lineup", "away_lineup", "ft_trip_no", "order_override")


@dataclass
class Enriched:
    tables: dict[str, pl.DataFrame]


def enrich(staged: dict[str, pl.DataFrame], overrides_dir: Path | None = None) -> Enriched:
    ev_all, mp, mt, sh_all = staged["events"], staged["match_players"], staged["match_teams"], staged["shots"]
    rows: dict[str, list[dict]] = {k: [] for k in ("events_enriched", "stints", "possessions", "shots_enriched",
                                                   "ft_trips", "enrich_issues")}
    events_by_match = ev_all.partition_by("match_id", as_dict=True)
    shots_by_match = sh_all.partition_by("match_id", as_dict=True)
    for (match_id,), g in sorted(events_by_match.items()):
        teams_df = mt.filter(pl.col("match_id") == match_id).sort("is_home", descending=True)
        home, away = teams_df["team_season_id"].to_list()
        players = mp.filter((pl.col("match_id") == match_id) & pl.col("starter"))
        starters = {t: set(players.filter(pl.col("team_season_id") == t)["player_id"]) for t in (home, away)}

        events, _ = order_events(g.to_dicts())
        overridden: set[int] = set()
        ov_path = overrides_dir / f"{match_id}.json" if overrides_dir else None
        if ov_path and ov_path.exists():
            events, ov_log = apply_overrides(events, json.loads(ov_path.read_text()))
            for fix in ov_log:
                overridden |= set(fix["actions"])
                rows["enrich_issues"].append(_issue(match_id, None, "override", fix["fix"],
                                                    note=f"{fix['actions']} after {fix['after']}: {fix['reason']}"))

        lu = track(match_id, events, starters)
        trips, trip_of = build_trips(match_id, events)
        poss, anomalies = build_possessions(match_id, events, trips, trip_of, (home, away))
        shots = enrich_shots(shots_by_match[(match_id,)].to_dicts() if (match_id,) in shots_by_match else [],
                             events, trips, poss)

        for e in events:
            e["ft_trip_no"] = trip_of.get(e["action_number"])
            e["order_override"] = e["action_number"] in overridden
        seq_poss = {e["seq"]: e["possession_no"] for e in events}
        for t in trips:
            t["possession_no"] = seq_poss.get(t["first_seq"])
        _stint_possessions(lu.stints, poss)

        rows["events_enriched"] += events
        rows["stints"] += lu.stints
        rows["possessions"] += poss
        rows["shots_enriched"] += shots
        rows["ft_trips"] += trips
        rows["enrich_issues"] += [_issue(match_id, i, "lineup", i["issue"]) for i in lu.issues]
        rows["enrich_issues"] += [_issue(match_id, a, "possession", a["issue"]) for a in anomalies]

    tables = {name: _frame(name, rs) for name, rs in rows.items()}
    log.info("enrich.done", **{k: v.height for k, v in tables.items()})
    return Enriched(tables)


def _stint_possessions(stints: list[dict], poss: list[dict]) -> None:
    """Possessions played by each stint, by the five on court when the possession began."""
    by_team: dict[int, list[dict]] = {}
    for s in stints:
        s["poss_for"] = s["poss_against"] = 0
        by_team.setdefault(s["team_season_id"], []).append(s)
    for team, ss in by_team.items():
        ss.sort(key=lambda s: s["start_seq"])
        for p in poss:
            st = None
            for s in ss:  # last stint that started at or before the possession
                if s["start_seq"] <= p["start_seq"]:
                    st = s
                else:
                    break
            if st is not None:
                st["poss_for" if p["team_season_id"] == team else "poss_against"] += 1


def _issue(match_id: int, src: dict | None, source: str, kind: str, note: str | None = None) -> dict:
    src = src or {}
    return {"match_id": match_id, "source": source, "issue": kind, "action_number": src.get("action_number"),
            "seq": src.get("seq"), "team_season_id": src.get("team_season_id"), "player_id": src.get("player_id"),
            "period_label": src.get("period_label"), "clock": src.get("clock"), "note": note}


def _frame(name: str, rows: list[dict]) -> pl.DataFrame:
    model = schemas.ENRICHED_TABLES[name]
    sch = schemas.polars_schema(model)
    df = pl.DataFrame([{k: r.get(k) for k in sch} for r in rows], schema=sch, orient="row")
    try:
        return model.validate(df, lazy=True)
    except Exception as e:
        raise ValueError(f"enriched table {name!r} failed its schema:\n{e}") from e
