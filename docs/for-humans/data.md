# Data reference

Every table the pipeline writes: where it lives, what one row is, and what is in
it. The reasons behind the rules are in [methods.md](methods.md).

All layers live under `$TBL_DATA_DIR` (default `~/data/tbl`) as Hive-partitioned
Parquet, `<layer>/league=tbl/season=<key>/<table>.parquet`, and every table is
also a DuckDB view in `~/data/tbl/tbl.duckdb`:

```bash
uv run tbl sql "SELECT season, COUNT(*) FROM matches GROUP BY ALL"
```

## Keys

| Key | Meaning | Persistent across seasons |
|---|---|---|
| `match_id` | TBF matchId | - |
| `genius_id` | FIBA LiveStats game id (on `matches`) | - |
| `player_id` | TBF person id (`kisiID`) | yes, also across leagues |
| `team_season_id` | TBF teamProcessId: one club in one season | no |
| `club_id` | TBF teamId | yes |

## Staged tables (`tbl build`)

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

Genius players are mapped to TBF ids by (team, jersey); where TBF's box lacks the jersey,
by name within the team (ASCII-folded surname, then first name), and a player TBF omits
from one game's box gets his season id if his name is unique in the season. Column names
are English; TBF field quirks (`twoPointShots` = 2P made, `rivalFoul` = fouls drawn, ...)
are decoded in `parse/tbf.py`. `matches.forfeit` marks games played but scored 20-0 at the
table (2025-26: Yalovaspor, 7 games); scores there are official, analysis uses the played score.

`tbl build` fails if a blocking check fails: 1:1 mapping of every player who played,
mapped Genius and TBF names share a word, points add up, per-period points sum to the
final, shots = FGA, TBF game-flow is a subset of Genius pbp, no unseen source fields.
Informational notes: TBF box score vs Genius (2025-26 week 1 TBF box is stale; Genius is
kept), TBF minutes misattributions, incomplete TBF shot charts, forfeits, and old or
national-team career rows without shot splits.

### Column conventions (verified against the FIBA Statisticians' Manual 2024)

- `eff` = FIBA efficiency (Genius `eff_1`, TBF `efficiency`). Team `eff` includes team
  rebounds and subtracts team turnovers. PIR / Game Score are not stored; derive them
  (Genius `eff_5` double-subtracts technicals, `eff_2`/`eff_4` are not standard metrics).
- `pf` = every foul by the player incl. technical/unsportsmanlike/disqualifying. Team
  `coach_bench_fouls` = coach/bench technicals; `fouls_total` = `pf` + `coach_bench_fouls`.
- `fga_blocked` = player's own shots that were blocked. `fouls_drawn` = fouls received.
- Team `oreb`/`dreb`/`reb`/`tov` include team rebounds/turnovers (`team_reb_*`, `team_tov`).
- In `events`, rebounds with `sub_type = 'offensivedeadball'` are not rebounds (ball not
  live after a missed non-final FT); team rebounds/turnovers have `player_id` null.
- `pts_paint` = made 2pt shots in the restricted area (no FTs); `times_tied` is the raw
  Genius counter (it does not always count a score level at a period break).
- Sort play-by-play by `(period_ord, elapsed_s, action_number)`: ~0.3% of actions are
  entered late, so action_number alone can run the clock backwards.
- Shots: `x`, `y` are 0–100 of the full 28 × 15 m court; teams switch ends at halftime
  and keep the 2nd-half end in OT. 181 tip-ins sit at a fixed spot (6, 50)/(94, 50).


## Enriched tables (`tbl build`)

`~/data/tbl/enriched/league=tbl/season=2026-27/*.parquet`, same DuckDB file.

| Table | Grain | What it adds |
|---|---|---|
| `events_enriched` | event | canonical `seq`, `possession_no`, `offense_team_id`, `home_lineup`/`away_lineup`, `ft_trip_no` |
| `stints` | team x unchanged five | `lineup`, seconds, points for/against, possessions for/against |
| `possessions` | possession | offense team, start/end type, duration, points, FGA/FTA/OREB/TOV |
| `shots_enriched` | shot | metres from the attacked basket, `distance_m`, `zone`, assisted/blocked/fouled (and-one), second chance, fast break, seconds into the possession, score margin before the shot, both fives |
| `ft_trips` | free-throw trip | shooter, foul that caused it, type (and-one, shooting, penalty, technical, unsportsmanlike) |
| `enrich_issues` | issue | reordered actions, lineup/possession anomalies, applied overrides |


## Analysis tables (`tbl analyze`)

`tbl analyze` reads the staged and enriched layers and writes
`analysis/league=tbl/season=<key>/*.parquet` (same DuckDB file) and `metrics.json`
(model scores, matchup backtest, weights). If the season before has been analysed, it
is used too: xPTS training shots, stabilization points, player priors, the matchup
error used for win probabilities. So analyse seasons in order.

| Table | Grain | What it holds |
|---|---|---|
| `shot_xpts` | shot | `xfg` (make probability), `xpts`, `transition`, shot `family` |
| `xpts_coefficients` | model term | logistic regression coefficients (log-odds per SD) |
| `player_shooting`, `player_zone_shooting`, `team_shooting` | player / player x zone / team x offense-defense | points vs xPTS: `poe`, `poe_per_shot` ± `poe_se`, shot quality `xpts_per_shot` |
| `team_profiles` | team (+ a league row) | pace, ORtg/DRtg/net on counted possessions, four factors for and against, shot diet by zone, xPTS per shot |
| `possession_starts` | team x offense/defense x start | possessions and points per possession by how they start |
| `player_profiles` | player x team | per game and per 40, TS%, eFG%, USG%, AST%, ORB%/DRB%, STL%, BLK%, TOV%, on/off, xPTS shot quality and making |
| `player_percentiles` | player x metric | percentile among players with 100+ minutes |
| `pairs` | team x two players | seconds, possessions, ortg/drtg/net together and each one without the other |
| `assist_pairs` | passer -> scorer | assisted baskets, points, by zone |
| `score_timeline` | scoring action | running score and margin, plus 0-0 and the final |
| `game_flow` | team-game | biggest lead and run, lead changes, ties, time leading, largest deficit |
| `runs` | run of 8+ points | team, points, start/end time, margin before and after |
| `clutch_possessions`, `clutch_team`, `clutch_player` | possession / team / player | last 5 min of Q4/OT within 5 points |
| `xpts_calibration` | model x bin | predicted vs observed FG% in ten bins, with log loss, Brier, calibration slope and intercept per model |
| `player_games` | player x game | box score, on-court counts, team totals and shot xPTS: every player rate sums from these |
| `stat_reliability` | rate | sampling noise `sigma`, true spread `tau`, stabilization point `k` (90% bootstrap interval), `k` in games, split-half check |
| `prior_backtest`, `prior_weights` | rate x games / rate group | first 2/4/8 games -> rest of season: RMSE raw, padded, with last season; tuned weights `d` |
| `player_stable` | player x team x rate | raw rate and units, last season's rate and source, padded estimate with a 90% interval, weights |

Opponent scouting (`matchup.py`):

| Table | Grain | What it holds |
|---|---|---|
| `team_ratings` | team | raw and opponent-adjusted offensive/defensive/net rating (ridge fit with home court), pace |
| `matchup_pairs` | team x opponent x venue | projected pace, points per 100 for both sides, score, margin with an 80% range, win probability, the next fixture between them |
| `matchup_backtest` | game | walk-forward check: margin predicted from the games before it vs actual |
| `matchup_factors` | team x opponent x direction x factor | four factors: attacker's value and rank, what the defence allows and its rank, league value, expected value, impact in points per 100 |
| `matchup_zones` | team x zone x offense/defense | shot share, FG% and xPTS per shot by zone, for and against |
| `team_identity` | team x metric | season value, league rank, style flag and reliability tier |
| `team_reliability` | team stat | ICC (share of between-team variance), games for half reliability, reliability now, tier (`oturdu` / `kısmen` / `gürültü`) |
| `rotation` | player x period | minutes per team game by quarter and in the last 5 minutes, games and starts |
| `top_lineups` | team x five | most-used fives: seconds, possessions, points, net |
| `period_profile` | team x period | points scored and allowed per game by quarter |
| `team_form` | team-game | margin, expected margin from season ratings, residual |
| `head_to_head` | team x opponent x game | meetings in this and last season |

## Tableau files

`tbl analyze` also writes flat CSVs with a Hyper extract each (Tableau Public saves only
extracts), the court image, team logos and themes, and the generated workbook, to
`tableau/tbl-<key>/`. Names sit next to ids, so the workbook needs no joins. Viewer-facing
text is pre-formatted in Turkish (`presentation.py`), and `glossary.py` adds a
Turkish name and definition (`term_tr`, `term_desc`) to every metric row for tooltips.
The `mx_*` files are the opponent page; `home_table`, `team_theme` and `theme_band`
serve the home page and the page chrome. See [dashboards.md](dashboards.md).
