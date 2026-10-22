import json

import duckdb
import polars as pl
import pytest
from conftest import FIXTURES, needs_fixtures

from tbl_data import build as B
from tbl_data import validate as V
from tbl_data.config import get_season
from tbl_data.parse import genius as G
from tbl_data.parse.common import minutes_to_s
from tbl_data.parse.drift import Drift
from tbl_data.store.reader import RawReader

S = get_season("tbl", "2026-27")


def test_clock_and_minutes():
    assert G.clock_s("00:01:70") == pytest.approx(1.7)
    assert G.clock_s("09:39") == 579
    assert minutes_to_s("46:60") == 47 * 60
    assert minutes_to_s("00:23:29") == 1409
    assert minutes_to_s("") == 0


def test_period_ordinals():
    assert [G.period_ord(p, t, 4) for p, t in [(4, "REGULAR"), (1, "OVERTIME"), (2, "OVERTIME")]] == [4, 5, 6]
    assert [G.period_label(o, 4) for o in (1, 4, 5, 6)] == ["Q1", "Q4", "OT1", "OT2"]


@needs_fixtures
def test_double_overtime_match_parses():
    doc = json.loads((FIXTURES / "genius/livestats/2900501.json").read_text())
    ev = G.event_rows(doc, 345923, Drift())
    assert [e["action_number"] for e in ev] == sorted(e["action_number"] for e in ev)
    assert {e["period_label"] for e in ev} == {"Q1", "Q2", "Q3", "Q4", "OT1", "OT2"}
    assert max(e["elapsed_s"] for e in ev) == 4 * 600 + 2 * 300
    per = G.period_rows(ev, 4)
    assert sum(p["pts"] for p in per if p["tno"] == 1) == doc["tm"]["1"]["score"]
    assert sum(p["pts"] for p in per if p["tno"] == 2) == doc["tm"]["2"]["score"]
    assert G.match_info(doc, Drift(), "x")["n_overtimes"] == 2


@needs_fixtures
def test_drift_reports_unseen_fields():
    doc = json.loads((FIXTURES / "genius/livestats/2939393.json").read_text())
    doc["pbp"][0]["newField"] = 1
    d = Drift()
    G.event_rows(doc, 1, d)
    assert d.rows() == [{"object": "genius.event", "key": "newField", "n": 1, "first_seen": f"1/a{doc['pbp'][0]['actionNumber']}"}]


@pytest.fixture
def staged(env):
    env().run()
    reader = RawReader(env.store, env.manifest, "tbl/2026-27")
    played = [(r["match_id"], int(r["genius_id"]) if r["genius_id"] else None)
              for r in env.manifest.matches("tbl/2026-27", played_only=True)]
    return B.build(reader, S, played)


@needs_fixtures
def test_build_joins_sources(staged):
    t = staged.tables
    # 345923 has Genius but no TBF match files in the fixtures -> skipped
    assert t["matches"]["match_id"].to_list() == [345960]
    m = t["matches"].row(0, named=True)
    assert (m["home_score"], m["away_score"], m["attendance"], m["genius_id"]) == (103, 84, 850, 2939393)
    assert m["referee_1"] == "NİHAT ÇETİN"
    assert t["match_players"].height == 22 and t["match_players"]["player_id"].null_count() == 0
    sivas = t["match_players"].filter(pl.col("player_id") == 316467).row(0, named=True)
    assert (sivas["pts"], sivas["fg3m"], sivas["fg3a"], sivas["jersey"]) == (15, 5, 8, "4")
    # events/shots carry TBF person ids
    ev = t["events"]
    assert ev.filter(pl.col("pno").is_not_null())["player_id"].null_count() == 0
    assert t["shots"].height == 126 and t["shots"]["player_id"].null_count() == 0
    assert staged.drift == [] and staged.unmapped == []


@needs_fixtures
def test_validation_passes_on_fixture(staged):
    checks = V.run_all(staged.tables, pl.DataFrame(staged.drift, schema=["object", "key", "n", "first_seen"]),
                       staged.unmapped)
    failing = {c.name: c.failures for c in checks if c.blocking and not c.ok}
    assert failing == {}


@needs_fixtures
def test_validation_catches_box_mismatch(staged):
    t = dict(staged.tables)
    t["tbf_box"] = t["tbf_box"].with_columns(
        pl.when(pl.col("player_id") == 316467).then(pl.col("ast") + 1).otherwise(pl.col("ast")).alias("ast"))
    c = V.box_tbf_vs_genius(t)
    assert c.failures.select("player_id", "stat", "genius", "tbf").rows() == [(316467, "ast", 5, 6)]


def test_schema_rejects_bad_rows():
    with pytest.raises(ValueError, match="shots"):
        B._frame("shots", [{"match_id": 1, "action_number": 1, "team_season_id": 1, "is_home": True,
                            "player_id": 1, "pno": 1, "period_ord": 1, "x": 140.0, "y": 50.0, "made": True,
                            "shot_type": "2pt", "sub_type": None, "previous_action": None}])


@needs_fixtures
def test_write_creates_duckdb_views(staged, tmp_path):
    out = B.write(staged, tmp_path, S)
    assert (out / "events.parquet").exists()
    con = duckdb.connect(str(tmp_path / "tbl.duckdb"), read_only=True)
    n, season = con.execute("SELECT count(*), any_value(season) FROM match_players").fetchone()
    con.close()
    assert (n, season) == (22, "2026-27")


def test_no_table_column_clashes_with_hive_keys():
    from tbl_data import schemas

    for name, model in schemas.TABLES.items():
        assert not set(schemas.polars_schema(model)) & B.HIVE_KEYS, name
