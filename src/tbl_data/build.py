"""raw -> staged: parse every stored document, join Genius to TBF ids, validate, write Parquet.

Genius is canonical for box score, events and shots; TBF for ids, names, officials,
video. Genius players are tied to TBF person ids through (team, jersey), which is
unique within a team in every match (checked in validate.player_mapping). Where TBF has
no jersey (early 2025-26 games) or it differs, the name decides: same team and game, same
surname ("I. Pinarer" = "İbrahim Pınarer"; the first initial breaks ties), and only if
exactly one TBF player of that team fits. A player Genius has but TBF's box score lacks
(a TBF omission) gets a stable negative id from his team and name, so lineups and box
scores stay whole; he is listed in players with the Genius name only.
"""

from __future__ import annotations

import re
import unicodedata
import zlib
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

HIVE_KEYS = {"league", "season"}


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
    synthetic: dict[int, dict] = {}  # negative id -> players row for players TBF does not list

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
        by_jersey = {(r["team_season_id"], r["jersey"]): r["player_id"] for r in tbf_box if r["jersey"]}
        players = [p | {"team": team_of[p.pop("tno")]} for p in G.player_rows(g, match_id, drift)]
        mapped = {i: by_jersey.get((p["team"], p["jersey"])) for i, p in enumerate(players)}
        taken = {v for v in mapped.values() if v}
        for i, p in enumerate(players):
            if mapped[i] is None:
                mapped[i] = _match_by_name(p["name"], [r for r in tbf_box if r["team_season_id"] == p["team"]
                                                       and r["player_id"] not in taken])
                if mapped[i]:
                    taken.add(mapped[i])

        pid_of: dict[tuple[int, int], int] = {}
        for i, p in enumerate(players):
            team, pid = p.pop("team"), mapped[i]
            if pid is None and (p.get("seconds") or 0) > 0:  # played, but TBF's box score lacks him
                pid = -(zlib.crc32(f"{team}|{' '.join(_fold(p['name']))}".encode()) % 1_000_000_000)
                synthetic.setdefault(pid, {"player_id": pid, "full_name": p["name"], "team_season_id": team,
                                           "jersey": p["jersey"]})
                unmapped.append({"match_id": match_id, "team_season_id": team, "jersey": p["jersey"],
                                 "name": p["name"], "player_id": pid})
            if pid is None:  # did not play: no stats to keep
                continue
            pid_of[(team, p["pno"])] = pid
            rows["match_players"].append(p | {"team_season_id": team, "player_id": pid})

        for t in G.team_rows(g, match_id, drift):
            tno = t.pop("tno")
            # TBF's coach is the registered name; Genius' is typed per game (case/spelling vary)
            coach = hdr["home_coach" if tno == 1 else "away_coach"] or t["coach"]
            rows["match_teams"].append(t | {"team_season_id": team_of[tno], "is_home": tno == 1, "coach": coach})

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

    # FIBA forfeit: the official result is 20-0 while Genius keeps the game as played. Analysis
    # uses the played score; matches keeps the official one
    played_pts = {(r["match_id"], r["team_season_id"]): r["pts"] for r in rows["match_teams"]}
    for m in rows["matches"]:
        m["forfeit"] = ({m["home_score"], m["away_score"]} == {20, 0}
                        and played_pts.get((m["match_id"], m["home_team_id"])) != m["home_score"])

    rows["teams"] = T.team_rows(reader.get("tbf", "teams", str(season.league_id)), drift)
    rows["players"] = _players(reader, season, drift, {r["club_id"]: r["team_season_id"] for r in rows["teams"]})
    # a player TBF omitted from one game's box score but lists elsewhere in the season (often after a
    # transfer): if his name is unique across the season's TBF box scores, use that person id
    season_ids: dict[tuple, set] = {}
    for r in rows["tbf_box"]:
        season_ids.setdefault(tuple(_fold(r["name"] or "")), set()).add(r["player_id"])
    remap = {neg: ids.pop() for neg, row in synthetic.items()
             if len(ids := set(season_ids.get(tuple(_fold(row["full_name"])), set()))) == 1}
    if remap:
        for table in ("match_players", "events", "shots"):
            for r in rows[table]:
                if r.get("player_id") in remap:
                    r["player_id"] = remap[r["player_id"]]
    rows["players"] += [r for neg, r in synthetic.items() if neg not in remap]
    unmapped = [u for u in unmapped if u.pop("player_id", None) not in remap]
    for rid, doc in reader.iter("tbf", "player_profile"):
        rows["player_career"] += T.career_rows(doc, int(rid), drift)

    tables = {name: _frame(name, rs) for name, rs in rows.items()}
    log.info("build.done", **{k: v.height for k, v in tables.items()}, drift=len(drift.unknown), unmapped=len(unmapped))
    return Staged(tables, drift.rows(), unmapped)


def _players(reader: RawReader, season: Season, drift: Drift, team_of_club: dict[int, int]) -> list[dict]:
    """TBF season player list, completed and overridden by individual profiles."""
    lst = T.player_list_rows(reader.get("tbf", "player_list", f"{season.season_id}_{season.league_id}"), drift)
    for r in lst:  # the list gives the persistent club id; staged tables use the club-season id
        r["team_season_id"] = team_of_club.get(r.pop("club_id"))
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
def write(staged: Staged, root: Path, season: Season, enriched: dict[str, pl.DataFrame] | None = None) -> Path:
    """Write Parquet under {staged,enriched}/league=../season=../ and refresh DuckDB views."""
    out = root / "staged" / f"league={season.league}" / f"season={season.key}"
    for name, df in {**staged.tables, **(enriched or {})}.items():
        # DuckDB's hive partition values silently replace same-named file columns
        if clash := set(df.columns) & HIVE_KEYS:
            raise ValueError(f"table {name!r} has columns {clash} that clash with hive partition keys")
    out.mkdir(parents=True, exist_ok=True)
    for name, df in staged.tables.items():
        df.write_parquet(out / f"{name}.parquet")
    pl.DataFrame(staged.drift, schema={"object": pl.String, "key": pl.String, "n": pl.Int64, "first_seen": pl.String}
                 ).write_parquet(out / "schema_drift.parquet")
    if enriched:
        eout = root / "enriched" / f"league={season.league}" / f"season={season.key}"
        eout.mkdir(parents=True, exist_ok=True)
        for name, df in enriched.items():
            df.write_parquet(eout / f"{name}.parquet")
    refresh_views(root)
    return out


def read_layer(root: Path, layer: str, season: Season) -> dict[str, pl.DataFrame]:
    d = root / layer / f"league={season.league}" / f"season={season.key}"
    if not d.exists():
        raise FileNotFoundError(f"{d} not found; run `tbl build` first")
    return {p.stem: pl.read_parquet(p) for p in sorted(d.glob("*.parquet"))}


def write_layer(root: Path, layer: str, season: Season, tables: dict[str, pl.DataFrame]) -> Path:
    out = root / layer / f"league={season.league}" / f"season={season.key}"
    for name, df in tables.items():
        if clash := set(df.columns) & HIVE_KEYS:
            raise ValueError(f"table {name!r} has columns {clash} that clash with hive partition keys")
    out.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.write_parquet(out / f"{name}.parquet")
    refresh_views(root)
    return out


def refresh_views(root: Path) -> None:
    con = duckdb.connect(str(root / "tbl.duckdb"))
    try:
        for layer in ("staged", "enriched", "analysis"):
            for name in sorted({p.stem for p in (root / layer).glob("*/*/*.parquet")}):
                glob = (root / layer / "*" / "*" / f"{name}.parquet").as_posix()
                con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{glob}', "
                            "hive_partitioning = true, union_by_name = true)")
    finally:
        con.close()


_SUFFIX = {"jr", "sr", "ii", "iii", "iv"}


def _fold(name: str) -> list[str]:
    """'İbrahim Pınarer' -> ['ibrahim', 'pinarer']: lower case, no accents, no suffixes."""
    name = name.replace("İ", "I").replace("ı", "i")
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return [t for t in re.split(r"[^a-z]+", name) if t and t not in _SUFFIX]


def _match_by_name(genius_name: str, candidates: list[dict]) -> int | None:
    """Genius 'I. Pinarer' against TBF full names of one team in one game; None unless unique."""
    g = _fold(genius_name or "")
    if not g:
        return None
    folded = {c["player_id"]: t for c in candidates if (t := _fold(c.get("name") or ""))}
    for surname in reversed(g[1:]):  # the last name first; "Medley-Bacon" also tries "Medley"
        same = {pid: t for pid, t in folded.items() if surname in t[1:]}
        if len(same) == 1:  # first names vary between sources ("Emin" / "Muhammed Emin")
            return next(iter(same))
        if same:  # several with that surname: a first name (or initial) shared with Genius decides
            hits = {pid for pid, t in same.items() if t[0] in g[:-1]} or \
                   {pid for pid, t in same.items() if t[0][0] == g[0][0]}
            return hits.pop() if len(hits) == 1 else None
    return None
