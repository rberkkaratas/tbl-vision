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
