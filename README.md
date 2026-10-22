# tbl-vision

Data pipeline for the Turkish Basketball League (TBL): combines TBF API data
(fixtures, persistent player IDs, referees, video links) with Genius / FIBA
LiveStats (play-by-play, shot x/y, box score) into raw → staged → enriched layers.
For personal, non-commercial analysis; raw data is not redistributed.

## Setup

```bash
uv sync
uv run pytest            # offline; needs tests/fixtures (not in git)
```

Data lives outside the repo: `$TBL_DATA_DIR` (default `~/data/tbl`).

```
~/data/tbl/
  raw/{source}/{kind}/{rid}/{sha256[:16]}.json.gz   # never overwritten
  manifest.sqlite                                    # resources, fetch_log, runs, matches
  browser-profile/                                   # persistent Chrome profile for TBF
```

## Usage

```bash
uv run tbl sync --season 2026-27          # everything due: fixtures, played matches, players
uv run tbl sync --max-requests 20         # small trial run
uv run tbl sync --only genius             # Genius only (needs fixtures already fetched)
uv run tbl status
uv run tbl build                          # raw -> staged Parquet + DuckDB views + validation (~3 s)
uv run tbl sql "SELECT * FROM matches LIMIT 5"
```

TBF sits behind Cloudflare, so `sync` opens a Chrome window (SeleniumBase CDP
mode) and calls the API with in-page `fetch()`, 2–4 s apart. The pipeline never
solves captchas: if a challenge does not clear on its own it waits for you to tick
the box in that window, and otherwise stops; the next `sync` resumes where it left off.

### Refetch rules

| Resource | Refetched |
|---|---|
| league weeks, teams, player list, fixture weeks | every 6 h (`--refresh-season` forces) |
| fixture week whose matches all settled | never |
| match (7 TBF endpoints + Genius) | every 2 h until 48 h after tip-off, then final |
| player profile / career / season games | once (`--refresh-players` forces) |
| 404 / empty | after 24 h · errors after 1 h |

Genius requests send `If-None-Match`; a 304 stores nothing new.

## Staged tables

`~/data/tbl/staged/league=tbl/season=2026-27/*.parquet`, exposed as DuckDB views in
`~/data/tbl/tbl.duckdb` (with `league`, `season` columns from the path).

| Table | Grain | Source |
|---|---|---|
| `matches` | match | TBF fixture + header (officials, video), Genius attendance / period lengths |
| `teams` | club-season (`team_season_id`, `club_id`) | TBF |
| `players` | person (`player_id`, persistent) | TBF player list + profiles |
| `match_teams` | team in match: box totals incl. team rebounds, second-chance / paint / bench points | Genius |
| `period_scores` | team x period (Q1..Q4, OT1..) | Genius pbp |
| `match_players` | player in match: box score, starter, `pno` | Genius, mapped to TBF ids |
| `events` | play-by-play action, `elapsed_s`, qualifiers, running score | Genius |
| `shots` | field goal attempt, raw x/y (0-100) | Genius |
| `player_career` | player x season x competition | TBF |
| `tbf_box`, `tbf_events`, `tbf_shots` | TBF copies, kept only for cross-checks | TBF |

Keys: `match_id` = TBF matchId, `player_id` = TBF person id, `team_season_id` = TBF teamProcessId.
Genius players are mapped to TBF ids by (team, jersey). Column names are English;
TBF field quirks (`twoPointShots` = 2P made, `rivalFoul` = fouls drawn, ...) are decoded in `parse/tbf.py`.

`tbl build` fails if a blocking check fails: TBF and Genius box scores equal, 1:1 player
mapping, points add up, per-period points sum to the final, shots = FGA, TBF game-flow
is a subset of Genius pbp, no unseen source fields. Informational notes (2026-27): one
TBF minutes misattribution (345930), one incomplete TBF shot chart (345937), and old or
national-team career rows without shot splits.
