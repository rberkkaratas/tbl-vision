from datetime import UTC, datetime
from pathlib import Path

import pytest

from tbl_data.config import get_season
from tbl_data.fetch.files import FileFetcher
from tbl_data.ingest import Syncer
from tbl_data.store import Manifest, RawStore

FIXTURES = Path(__file__).parent / "fixtures"

# Fixtures are real TBF/Genius responses and are kept out of git (no redistribution).
# Mini season: weeks 2 (OT match 345923, Genius only), 7 (345960, everything), 8 (unplayed).
needs_fixtures = pytest.mark.skipif(not (FIXTURES / "tbf").exists(), reason="tests/fixtures not present")


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        from datetime import timedelta

        self.t += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock(datetime(2026, 10, 22, 12, 0, tzinfo=UTC))


@pytest.fixture
def env(tmp_path, clock):
    """A Syncer factory wired to FileFetchers over the fixture dir."""
    store = RawStore(tmp_path / "raw")
    manifest = Manifest(tmp_path / "manifest.sqlite")
    tbf, genius = FileFetcher(FIXTURES, "tbf"), FileFetcher(FIXTURES, "genius")

    def make(**kw) -> Syncer:
        return Syncer(get_season("tbl", "2026-27"), store, manifest, tbf, genius, now=clock, **kw)

    make.store, make.manifest, make.tbf, make.genius = store, manifest, tbf, genius
    yield make
    manifest.close()
