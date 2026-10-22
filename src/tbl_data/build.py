"""raw -> staged: parse every stored document, join Genius to TBF ids, validate, write Parquet.

Genius is canonical for box score, events and shots; TBF for ids, names, officials,
video. Genius players are tied to TBF person ids through (team, jersey), which is
unique within a team in every match (checked in validate.player_mapping).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import polars as pl
import structlog

from . import schemas
from .config import Season
from .parse import genius as G
from .parse import tbf as T
from .parse.drift import Drift
from .store.reader import RawReader

log = structlog.get_logger()


@dataclass
class Staged:
    tables: dict[str, pl.DataFrame]
    drift: list[dict]
    unmapped: list[dict] = field(default_factory=list)  # Genius players with no TBF id


def build(reader: RawReader, season: Season, played_ids: list[tuple[int, int | None]]) -> Staged:
    """played_ids: (match_id, genius_id) of played matches, from the manifest."""
    drift = Drift()
    rows: dict[str, list[dict]] = {name: [] for name in schemas.TABLES}
    unmapped: list[dict] = []

    phases = {int(w["sezon_Hafta"]): w.get("devre_Deger")
              for w in T.data(reader.get("tbf", "league_weeks", str(season.league_id))) or []}
    fixture: dict[int, dict] = {}
    for rid, doc in reader.iter("tbf", "fixture_week"):
        week = int(rid.split("_w")[1])
        for r in T.fixture_rows(doc, week, phases.get(week), drift):
            fixture[r["match_id"]] = r

    for match_id, genius_id in played_ids:
        mid = str(match_id)
        hdr_doc, box_doc = reader.get("tbf", "match_header", mid), reader.get("tbf", "match_boxscore", mid)
        g = reader.get("genius", "livestats", str(genius_id)) if genius_id else None
        if not (hdr_doc and box_doc and g):
            log.warning("build.skip_match", match_id=match_id, header=bool(hdr_doc), box=bool(box_doc), genius=bool(g))
            continue
        hdr = T.header_row(hdr_doc, drift)
        home, away = hdr["home_team_id"], hdr["away_team_id"]
        team_of = {1: home, 2: away}
        info = G.match_info(g, drift, mid)
        fx = fixture[match_id]
        rows["matches"].append(fx | {
            "venue": hdr["venue"] or fx["venue"],
            "attendance": info["attendance"] or hdr["attendance"],
            "home_team_id": home, "away_team_id": away,
            "home_score": hdr["home_score"], "away_score": hdr["away_score"],
            "n_overtimes": info["n_overtimes"], "period_minutes": info["period_minutes"],
            "overtime_minutes": info["overtime_minutes"],
            **{k: hdr[k] for k in ("referee_1", "referee_2", "referee_3", "commissioner")},
            "youtube_url": hdr["youtube_url"] or fx["youtube_url"], "broadcast": hdr["broadcast"] or fx["broadcast"],
        })

        # TBF box: the bridge from (team, jersey) to persistent person id
        tbf_box = T.box_rows(box_doc, match_id, home, away, drift)
        rows["tbf_box"] += tbf_box
        by_jersey = {(r["team_season_id"], r["jersey"]): r["player_id"] for r in tbf_box}

        pid_of: dict[tuple[int, int], int] = {}
        for p in G.player_rows(g, match_id, drift):
            team = team_of[p.pop("tno")]
            pid = by_jersey.get((team, p["jersey"]))
            if pid is None:
                unmapped.append({"match_id": match_id, "team_season_id": team, "jersey": p["jersey"], "name": p["name"]})
                continue
            pid_of[(team, p["pno"])] = pid
            rows["match_players"].append(p | {"team_season_id": team, "player_id": pid})

        for t in G.team_rows(g, match_id, drift):
            tno = t.pop("tno")
            rows["match_teams"].append(t | {"team_season_id": team_of[tno], "is_home": tno == 1})

        events = G.event_rows(g, match_id, drift)
        for p in G.period_rows(events, G.periods(g)[0]):
            rows["period_scores"].append({"match_id": match_id, "team_season_id": team_of[p.pop("tno")]} | p)
        for e in events:
            tno = e.pop("tno")
            team = team_of.get(tno)
            e["team_season_id"] = team
            e["player_id"] = pid_of.get((team, e["pno"])) if team and e["pno"] else None
        rows["events"] += events

        for s in G.shot_rows(g, match_id, drift):
            team = team_of[s.pop("tno")]
            pid = pid_of.get((team, s["pno"]))
            if pid is None:
                unmapped.append({"match_id": match_id, "team_season_id": team, "jersey": None, "name": f"shot pno {s['pno']}"})
                continue
            rows["shots"].append(s | {"team_season_id": team, "player_id": pid})

        if doc := reader.get("tbf", "match_gameflow", mid):
            rows["tbf_events"] += T.gameflow_rows(doc, match_id, drift)
        if doc := reader.get("tbf", "match_shotchart", mid):
            rows["tbf_shots"] += T.shotchart_rows(doc, match_id, drift)

    rows["teams"] = T.team_rows(reader.get("tbf", "teams", str(season.league_id)), drift)
    rows["players"] = _players(reader, season, drift)
    for rid, doc in reader.iter("tbf", "player_profile"):
        rows["player_career"] += T.career_rows(doc, int(rid), drift)

    tables = {name: _frame(name, rs) for name, rs in rows.items()}
    log.info("build.done", **{k: v.height for k, v in tables.items()}, drift=len(drift.unknown), unmapped=len(unmapped))
    return Staged(tables, drift.rows(), unmapped)


def _players(reader: RawReader, season: Season, drift: Drift) -> list[dict]:
    """TBF season player list, completed and overridden by individual profiles."""
    lst = T.player_list_rows(reader.get("tbf", "player_list", f"{season.season_id}_{season.league_id}"), drift)
    out = {r["player_id"]: r for r in lst}
    for rid, doc in reader.iter("tbf", "player_profile"):
        prof = T.profile_row(doc, int(rid), drift)
        base = out.get(prof["player_id"], {})
        merged = base | {k: v for k, v in prof.items() if v is not None}
        if base.get("nationality"):  # list has the federation spelling + ISO-ish code
            merged["nationality"] = base["nationality"]
        out[prof["player_id"]] = merged
    return list(out.values())


def _frame(name: str, rows: list[dict]) -> pl.DataFrame:
    model = schemas.TABLES[name]
    sch = schemas.polars_schema(model)
    df = pl.DataFrame([{k: r.get(k) for k in sch} for r in rows], schema=sch, orient="row")
    try:
        return model.validate(df, lazy=True)
    except Exception as e:
        raise ValueError(f"staged table {name!r} failed its schema:\n{e}") from e


# -- output ------------------------------------------------------------------------
def write(staged: Staged, root: Path, season: Season) -> Path:
    """Write Parquet under staged/league=../season=../ and refresh DuckDB views."""
    out = root / "staged" / f"league={season.league}" / f"season={season.key}"
    out.mkdir(parents=True, exist_ok=True)
    for name, df in staged.tables.items():
        df.write_parquet(out / f"{name}.parquet")
    pl.DataFrame(staged.drift, schema={"object": pl.String, "key": pl.String, "n": pl.Int64, "first_seen": pl.String}
                 ).write_parquet(out / "schema_drift.parquet")
    refresh_views(root)
    return out


def refresh_views(root: Path) -> None:
    con = duckdb.connect(str(root / "tbl.duckdb"))
    try:
        names = sorted({p.stem for p in (root / "staged").glob("*/*/*.parquet")})
        for name in names:
            glob = (root / "staged" / "*" / "*" / f"{name}.parquet").as_posix()
            con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{glob}', "
                        "hive_partitioning = true, union_by_name = true)")
    finally:
        con.close()
