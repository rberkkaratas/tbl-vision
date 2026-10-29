# Running the pipeline

## Setup

You need Python 3.12, [uv](https://docs.astral.sh/uv/) and, for syncing TBF, Google
Chrome.

```bash
uv sync
uv run pytest     # 33 tests run offline; 20 more need tests/fixtures/ and skip without it
```

The skipped tests run on real TBF and FIBA LiveStats responses (a mini season: weeks 2,
7 and 8 of 2026-27). Those responses are not redistributed, so `tests/fixtures/` is not
in git.

## Commands

| Command | What it does |
|---|---|
| `uv run tbl sync` | fetch everything due for one season into the raw store |
| `uv run tbl build` | raw → staged → enriched Parquet and DuckDB views, then validation |
| `uv run tbl analyze` | enriched → analysis tables, `metrics.json`, Tableau files, then validation |
| `uv run tbl sql "<query>"` | SQL against the DuckDB views |
| `uv run tbl status` | what is in the raw store for a season |

Every command takes `--season` (default `2026-27`), `--league` (default `tbl`) and
`--data-dir`. Useful `sync` options: `--max-requests 20` for a small trial run, `--only
genius` once fixtures are in, `--refresh-season` and `--refresh-players` to ignore the
refetch timers, and `--player-games` to also fetch each player's season game log.

A full season from scratch:

```bash
uv run tbl sync --season 2025-26
uv run tbl build --season 2025-26
uv run tbl analyze --season 2025-26
uv run tbl sync && uv run tbl build && uv run tbl analyze      # then 2026-27
```

**Analyse seasons in order.** `analyze` for 2026-27 reads the 2025-26 analysis for xPTS
training shots, stabilization points, player priors and the matchup error. Without it,
the season stands alone, and `analyze` says so.

Known seasons are in `src/tbl_data/config.py` (TBL 2018-19 to 2026-27, with TBF's league
and season ids). Adding one is a line there.

## Where the data goes

Everything lives outside the repo, in `$TBL_DATA_DIR` (default `~/data/tbl`):

```
~/data/tbl/
  raw/{source}/{kind}/{rid}/{sha256[:16]}.json.gz   every response, never overwritten
  manifest.sqlite                                   resources, fetch log, runs, matches
  browser-profile/                                  persistent Chrome profile for TBF
  staged/  enriched/  analysis/                     league=tbl/season=<key>/*.parquet
  tbl.duckdb                                        views over all layers
  assets/logos/                                     downloaded logos
  tableau/tbl-<key>/                                CSV + Hyper + court.png + tbl-<key>.twb
```

Because raw responses are content-addressed and never overwritten, `build` and
`analyze` can be rerun at any time without touching the network.

## How syncing works

TBF's site sits behind Cloudflare, so plain HTTP clients get 403. `sync` opens a real
Chrome window (SeleniumBase CDP mode), loads a TBF page, and calls the API with in-page
`fetch()`, 2-4 seconds apart. It never solves a captcha: if a challenge does not clear on
its own, it waits for you to tick the box in that window, and otherwise stops. The next
`sync` resumes where it left off. FIBA LiveStats is plain HTTPS; requests send
`If-None-Match`, and a 304 stores nothing new.

| Resource | Refetched |
|---|---|
| league weeks, teams, player list, fixture weeks | every 6 h (`--refresh-season` forces) |
| fixture week whose matches all settled | never |
| match (7 TBF endpoints + Genius) | every 2 h until 48 h after tip-off, then final |
| player profile / career / season games | once (`--refresh-players` forces) |
| 404 / empty | after 24 h · errors after 1 h |

Late stat corrections land in the 48 hours after a game, which is why a match becomes
final only then.

## Validation

`build` and `analyze` end with a table of checks. `ok` passes, `note` is informational,
and a failing blocking check makes the command exit with status 1 and prints the first
failing rows. Typical notes: TBF's box score disagreeing with Genius (Genius is kept), a
few +/- differences, forfeits.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `sync` waits on a Cloudflare page | tick the box in the Chrome window; if it keeps coming back, stop and run again later |
| `analyze` says the previous season "predates player_games" | run `uv run tbl analyze --season <previous>` first |
| a feed error breaks a check (for example a mistyped clock) | add a fix in `overrides/<match_id>.json`; see `overrides/345923.json` |
| `build` fails on `schema_drift` | a source added a field: decide whether to parse it, then add it to `KNOWN` in `parse/drift.py` |
| logos missing in the workbook | `analyze` prints why; the workbook falls back to team colours from TBF |
| Tableau rejects the workbook | the error names the element; see [../for-ai/tableau-xml.md](../for-ai/tableau-xml.md) |
