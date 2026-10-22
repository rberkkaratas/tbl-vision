import httpx
import respx

from conftest import FIXTURES, needs_fixtures

from tbl_data import sources
from tbl_data.config import get_season
from tbl_data.fetch import Resource
from tbl_data.fetch.browser import is_challenge
from tbl_data.fetch.http import HttpxFetcher
from tbl_data.store import RawStore

S = get_season("tbl", "2026-27")


def test_raw_store_is_content_addressed(tmp_path):
    store = RawStore(tmp_path)
    a = store.put("tbf", "match_header", "1", b'{"a":1}')
    assert store.put("tbf", "match_header", "1", b'{"a":1}') == a
    b = store.put("tbf", "match_header", "1", b'{"a":2}')
    assert a != b
    assert len(list((tmp_path / "tbf" / "match_header" / "1").iterdir())) == 2
    assert store.get("tbf", "match_header", "1", a) == b'{"a":1}'


@needs_fixtures
def test_challenge_detection():
    page = (FIXTURES / "challenge.html").read_text()
    assert is_challenge(200, {}, page)
    assert is_challenge(403, {"cf-mitigated": "challenge"}, "")
    assert not is_challenge(200, {"content-type": "application/json"}, '{"data":[]}')


def test_urls():
    assert sources.fixture_week(S, "7", None).url.endswith(
        "Match/get-all-matches-for-filter?ActivityId=22215&Page=1&PageSize=-1&WeekFilter=7")
    assert "HalfValue=Playoff+Final" in sources.fixture_week(S, "38", "Playoff Final").url
    games = sources.player(S, 316467)[2]
    assert (games.method, games.rid) == ("POST", "174_316467")
    assert sources.genius_match("2939393").url == (
        "https://fibalivestats.dcd.shared.geniussports.com/data/2939393/data.json")


@respx.mock
def test_httpx_fetcher_etag_and_retry():
    url = "https://fibalivestats.dcd.shared.geniussports.com/data/1/data.json"
    route = respx.get(url).mock(side_effect=[
        httpx.Response(503),
        httpx.Response(200, json={"tm": {}}, headers={"ETag": '"abc"'}),
        httpx.Response(304),
    ])
    f = HttpxFetcher(min_interval=0)
    res = Resource("genius", "livestats", "1", url)
    r = f.fetch(res)
    assert r.ok and r.headers["etag"] == '"abc"'
    r = f.fetch(res, etag='"abc"')
    assert r.not_modified
    assert route.calls[-1].request.headers["If-None-Match"] == '"abc"'
    f.close()
