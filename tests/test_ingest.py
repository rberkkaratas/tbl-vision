from datetime import UTC, datetime

from conftest import needs_fixtures

from tbl_data.fetch import FetchResult

pytestmark = needs_fixtures

SEASON = 4  # league_weeks, league_info (missing in fixtures), teams, player_list
WEEKS = 3
MATCHES = 2 * (1 + 7)  # two played matches x (Genius + 7 TBF endpoints)
PLAYERS = 22 * 2  # 345960 box score has 22 players with TBF ids; profile + career (game log is opt-in)


def kind_status(m, kind):
    return dict(m.db.execute("SELECT status, COUNT(*) FROM resources WHERE kind=? GROUP BY status", (kind,)).fetchall())


def test_first_run_fetches_everything_due(env):
    st = env().run()
    assert st.outcome == "ok"
    assert st.requests == SEASON + WEEKS + MATCHES + PLAYERS
    m = env.manifest
    played = {r["match_id"]: r for r in m.matches("tbl/2026-27", played_only=True)}
    assert set(played) == {345923, 345960}  # week 8 match is in the future
    assert played[345960]["genius_id"] == "2939393"
    assert kind_status(m, "livestats") == {"ok": 2}
    assert kind_status(m, "match_header") == {"ok": 1, "missing": 1}  # no TBF fixtures for 345923
    assert kind_status(m, "player_profile") == {"ok": 1, "missing": 21}


def test_second_run_makes_no_requests(env):
    env().run()
    st = env().run()
    assert st.requests == 0
    assert st.outcome == "ok"


def test_settled_matches_are_final(env, clock):
    env().run()
    clock.advance(hours=25)  # past season/fixture TTLs and the missing-retry window
    env.tbf.calls.clear()
    env.genius.calls.clear()
    st = env().run()
    assert env.genius.calls == []  # both Genius docs final
    kinds = {(c.kind, c.rid) for c in env.tbf.calls}
    assert ("match_header", "345960") not in kinds
    assert ("player_profile", "316467") not in kinds
    assert ("match_header", "345923") in kinds  # missing -> retried after 24 h
    assert st.new_versions == 0


def test_settled_fixture_week_becomes_final(env, clock):
    env().run()
    clock.advance(hours=7)
    env().run()  # refetch week 7; now all its matches are settled
    final = dict(env.manifest.db.execute("SELECT rid, final FROM resources WHERE kind='fixture_week'").fetchall())
    assert final == {"22215_w02": 1, "22215_w07": 1, "22215_w08": 0}


def test_live_match_refetched_until_settled(env, clock):
    clock.t = datetime(2026, 10, 17, 16, 0, tzinfo=UTC)  # 3 h after 345960 tip-off (16:00 TR)
    env(with_players=False).run()
    st = env.manifest.state("tbf", "match_boxscore", "345960")
    assert not st.final
    clock.advance(hours=3)
    env.tbf.calls.clear()
    env(with_players=False).run()
    assert ("match_boxscore", "345960") in {(c.kind, c.rid) for c in env.tbf.calls}
    clock.advance(hours=48)
    env(with_players=False).run()
    assert env.manifest.state("tbf", "match_boxscore", "345960").final


def test_budget_stops_and_resumes(env):
    st = env(max_requests=5).run()
    assert (st.outcome, st.requests) == ("budget", 5)
    st = env().run()
    assert st.requests == SEASON + WEEKS + MATCHES + PLAYERS - 5


def test_only_genius(env):
    st = env(only="genius").run()
    assert st.requests == 0  # nothing known yet: fixtures come from TBF
    env(only="tbf").run()
    st = env(only="genius").run()
    assert st.requests == 2


def test_not_modified_keeps_previous_body(env, clock):
    env().run()
    sha = env.manifest.state("genius", "livestats", "2939393").sha256
    env.manifest.unfinalize("genius", "livestats")
    orig = env.genius.fetch
    env.genius.fetch = lambda res, etag=None: FetchResult(status=304)
    st = env(only="genius").run()
    env.genius.fetch = orig
    assert st.not_modified == 2
    after = env.manifest.state("genius", "livestats", "2939393")
    assert after.sha256 == sha and after.status == "ok"


def test_challenge_blocked_stops_run(env):
    from tbl_data.fetch import ChallengeBlocked

    def blocked(res, etag=None):
        raise ChallengeBlocked("test")

    env.tbf.fetch = blocked
    st = env().run()
    assert st.outcome == "blocked"
    assert env.manifest.db.execute("SELECT outcome FROM runs").fetchone()[0] == "blocked"
