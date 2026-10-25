"""League/season registry and runtime settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

TBF_ORIGIN = "https://www.tbf.org.tr"
GENIUS_DATA_URL = "https://fibalivestats.dcd.shared.geniussports.com/data/{genius_id}/data.json"


@dataclass(frozen=True)
class Season:
    league: str  # TBF url prefix, e.g. "tbl"
    key: str  # "2026-27"
    league_id: int  # TBF ActivityId / leagueId
    season_id: int

    @property
    def landing_url(self) -> str:
        # Page used to warm the browser session (Cloudflare cookies).
        start = self.key.split("-")[0]
        return f"{TBF_ORIGIN}/ligler/{self.league}-{start}-{int(start) + 1}"


SEASONS: dict[tuple[str, str], Season] = {
    (s.league, s.key): s
    for s in [
        Season("tbl", "2018-19", 11862, 158),
        Season("tbl", "2020-21", 13561, 162),
        Season("tbl", "2022-23", 16356, 166),
        Season("tbl", "2023-24", 17822, 168),
        Season("tbl", "2024-25", 19285, 170),
        Season("tbl", "2025-26", 20727, 172),
        Season("tbl", "2026-27", 22215, 174),
    ]
}


def get_season(league: str, key: str) -> Season:
    try:
        return SEASONS[(league, key)]
    except KeyError:
        known = ", ".join(f"{lg} {k}" for lg, k in SEASONS)
        raise KeyError(f"unknown season {league} {key}; known: {known}") from None


def previous_season(season: Season) -> Season | None:
    """The league's season before this one in the registry (None if it is the first)."""
    keys = sorted(k for lg, k in SEASONS if lg == season.league and k < season.key)
    return SEASONS[(season.league, keys[-1])] if keys else None


# Hand-written fixes per match (version-controlled, unlike the data)
OVERRIDES_DIR = Path(__file__).resolve().parents[2] / "overrides"


def data_dir() -> Path:
    """Data lives outside the repo; override with TBL_DATA_DIR."""
    return Path(os.environ.get("TBL_DATA_DIR", "~/data/tbl")).expanduser()
