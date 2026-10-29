# tbl-vision for AI agents

Read this before changing code. It is the operating manual: what must not break,
where things are, how to verify a change. For the reasoning behind any rule, the
human docs in `docs/for-humans/` have the long version (`methods.md` for models
and enrichment rules, `data.md` for every table).

## What this repo is

A Python 3.12 pipeline (uv, polars, DuckDB) for the Turkish Basketball League:
TBF API + FIBA LiveStats (Genius) → raw → staged → enriched → analysis → a Tableau
workbook generated as XML. CLI: `tbl` (`src/tbl_data/cli.py`, Typer).

## Hard rules

1. **No data in git.** Raw responses, Parquet, DuckDB, CSV/Hyper, logos and
   `tests/fixtures/` stay out (`.gitignore` covers them). Data lives in
   `$TBL_DATA_DIR` (default `~/data/tbl`). Never add a real TBF or Genius response to a
   test; tests that need one use `needs_fixtures` and skip without them.
2. **Polite fetching.** Do not shorten TBF's 2-4 s delays, parallelise requests, add
   captcha solving, or run `tbl sync` in a loop. Network runs are the human's call; ask
   before running `sync`.
3. **The raw store is immutable.** `store/raw.py` writes content-addressed files and never
   overwrites. Fix feed errors with `overrides/<match_id>.json` (logged to
   `enrich_issues`), never by editing raw files or silently patching in code.
4. **Validation is the contract.** `validate.py` checks return failing rows; blocking
   checks make `build`/`analyze` exit 1. A new rule or table gets a check. Do not
   downgrade a blocking check to make a run pass.
5. **Analyse seasons in order.** `analyze 2026-27` reads the 2025-26 analysis
   (`Prior`: xPTS training shots, stabilization points, priors, matchup σ).
6. **Coach-facing text never gives verdicts.** Strings in `presentation.py`,
   `glossary.py` and titles in `dashboards.py` describe numbers with league context and
   data strength. No "weak/strong/good/bad", "stop", "must"; use ranks ("1./17", 1 =
   highest), "eğilim", "ligin üst üçte birinde", and descriptive tags ("Topu çok
   kullanır"). Colour encodes direction ("ana takım yönünde"), not quality.
7. **Language split.** Code, comments, docs and commit messages are English.
   Viewer-facing dashboard text is Turkish with Turkish number formats; produce it with
   `presentation.num` / `presentation.pct` (decimal comma, `%` before the number).
8. **Workbook XML follows [tableau-xml.md](tableau-xml.md).** Tableau rejects unknown
   elements and wrong element order; most of those rules were learned the hard way.

## Repo map

```
src/tbl_data/
  cli.py            tbl sync | build | analyze | sql | status
  config.py         Season registry (TBF league/season ids), data_dir(), OVERRIDES_DIR
  sources.py        Resource builders for every remote document
  ingest.py         Syncer: what is due -> fetch -> raw store + manifest (refetch policy)
  fetch/            browser.py (TBF via SeleniumBase CDP, in-page fetch), http.py (Genius, ETag), files.py (offline)
  store/            raw.py (content-addressed store), manifest.py (SQLite), reader.py (latest body per resource)
  parse/            tbf.py, genius.py (pure: JSON -> row dicts), drift.py (KNOWN source fields)
  schemas.py        pandera contracts + polars dtypes for staged tables
  build.py          raw -> staged; Genius<->TBF id mapping; read_layer / write_layer / refresh_views
  validate.py       every cross-source and consistency check (Check objects)
  enrich/           order.py, lineups.py, possessions.py, ft_trips.py, shots.py; enrich() orchestrates
  analysis/
    __init__.py     analyze(staged, enriched, prior, fixtures) -> Analysis(tables, metrics); Prior
    xpts.py         shot model (out-of-fold, pooled with last season)
    profiles.py     team and player profiles, percentiles
    stability.py    stabilization points k, padded rates, prior backtest
    pairs.py flow.py  two-player views, assist network; game flow, runs, clutch
    matchup.py      ridge ratings, projections, backtest, four-factor matchups, team reliability tiers,
                    rotation, lineups, periods, form, head-to-head
    presentation.py Turkish viewer tables: augment(), reliability_views(), matchup_views(), scout_views()
    glossary.py     metric key -> (Turkish name, definition); annotate() adds term_tr/term_desc
    assets.py       logos (Genius tm.logoS, TBL from Wikipedia), Tableau shape palette, team colours
    tableau.py      export(): CSV + Hyper per table, court.png, team_theme, theme_band, logos
    dashboards.py   design(dir) -> Workbook: parameters, calcs, sheets, dashboards, actions
    workbook.py     dataclasses (Param, Calc, F, Source, Sheet, Zone, Dashboard, ParamAction, GoTo) + XML writer
overrides/          hand fixes per match (version-controlled)
tests/              pytest; conftest.needs_fixtures marks tests that need tests/fixtures/
docs/for-humans/    long-form docs; docs/for-ai/ this file + tableau-xml.md
```

## Commands

```bash
uv sync
uv run pytest                                  # 33 run without fixtures, 20 skip; with fixtures 53 pass
uv run pytest tests/test_matchup.py -q         # one file
uv run tbl build --season 2025-26              # offline: rebuilds from the raw store
uv run tbl analyze --season 2025-26 && uv run tbl analyze --season 2026-27
uv run tbl sql "SELECT * FROM possessions LIMIT 5"
```

`build` and `analyze` are offline and idempotent; they need an existing raw store.
`sync` needs the network, Chrome, and the human (Cloudflare).

## Data invariants you will trip over

- **Keys.** `match_id` (TBF), `player_id` (TBF person, persistent), `team_season_id`
  (club in one season), `club_id` (persistent club). Genius `pno`/`tno` exist only inside
  parsing; tm `"1"` is always TBF's home team.
- **Hive partition columns shadow table columns.** Paths carry `league=` and `season=`,
  so a table column named `league` or `season` is overwritten on read. Use other names
  (`league_value`, `season_key`, `career_season`).
- **Event order.** Use `events_enriched.seq`. Never sort play-by-play by `action_number`
  alone (~0.3% of actions are entered late).
- **Possessions.** Join events with `events_enriched.possession_no`, not seq ranges.
  Team ratings use box-score points over counted possessions; `possessions.pts` excludes
  technical FTs scored by the defence.
- **Shots.** `x`, `y` are 0-100 of the full court; use `shots_enriched` (metres from the
  attacked basket, zones). Genius `fastbreak` tags only makes; use `transition`.
- **Forfeits.** `matches.forfeit` games keep the official 20-0 score in staged tables;
  analysis uses the played score.
- **Sample size.** About 8 games per team in 2026-27. Anything per pair, lineup, clutch
  or quarter must carry its possession count or SE.

## Changing the dashboard

- Data for a page: add a table in `presentation.py` (Turkish text, pre-formatted), export
  it in `tableau.py`, add its glossary keys in `glossary.py` (SPEC/COL_SPEC).
- Sheets and layout: `dashboards.py`. Formula tokens resolved in `design()`: `[[RAKIP]]`
  (the Rakip parameter, "Sıradaki rakip" → the main team's next opponent), `[[MAC]]`
  (Maç 0 → the main team's last game), `GOSTER` (Görünüm: main team or opponent). Build
  formula strings with f-strings carefully: `{Ana takım}` is Tableau parameter syntax,
  `{MAC}` inside a non-f-string stays literal.
- Every page: `with_nav()` adds the page strip, team-coloured header band, logo and TBL
  logo; pages are `PAGE_H` tall (`RAKIP_H` for the scout page). Keep that height uniform,
  or the vertical scrollbar shifts the layout between pages.
- After a change: `uv run pytest tests/test_analysis.py`, then `uv run tbl analyze` and
  ask the human to open `~/data/tbl/tableau/tbl-2026-27/tbl-2026-27.twb` in Tableau
  Public. Tableau's error dialog names the element it rejects; map it with
  `tableau-xml.md`.

## Verifying claims

When you quote a number (docs, commit messages, dashboards), take it from a run, not
from memory or older docs: `metrics.json` in the analysis folder, or the validation
table `build`/`analyze` print. Many counts in the docs were measured on a snapshot and
drift as the season grows.

## Style

- polars expressions, not pandas; `c = pl.col` is common in analysis modules.
- Docstrings open each module with what it computes and the rule it follows, with the
  measurement that justified it (for example "beat raw ratings: MAE 9.2, Brier 0.199").
  Keep that habit: state the rule, then the evidence.
- Comments are sparse and explain why. Names are English; Turkish appears only in
  viewer text and Tableau field captions.
- Tests are small and synthetic where possible (`tests/test_matchup.py` builds round
  robins in code) so they run without fixtures.
