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
uv run tbl build                          # raw -> staged -> enriched Parquet + DuckDB views + validation
uv run tbl analyze                        # enriched -> analysis tables + Tableau CSVs + validation
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

## Enriched tables

`~/data/tbl/enriched/league=tbl/season=2026-27/*.parquet`, same DuckDB file.

| Table | Grain | What it adds |
|---|---|---|
| `events_enriched` | event | canonical `seq`, `possession_no`, `offense_team_id`, `home_lineup`/`away_lineup`, `ft_trip_no` |
| `stints` | team x unchanged five | `lineup`, seconds, points for/against, possessions for/against |
| `possessions` | possession | offense team, start/end type, duration, points, FGA/FTA/OREB/TOV |
| `shots_enriched` | shot | metres from the attacked basket, `distance_m`, `zone`, assisted/blocked/fouled (and-one), second chance, fast break, seconds into the possession, score margin before the shot, both fives |
| `ft_trips` | free-throw trip | shooter, foul that caused it, type (and-one, shooting, penalty, technical, unsportsmanlike) |
| `enrich_issues` | issue | reordered actions, lineup/possession anomalies, applied overrides |

Rules (measured on 2026-27):
- **Order**: game time; inside one clock second, the score Genius stamped on each action
  (it includes the action's own points), then action number. The stamp shows where a late
  entry belongs, and Genius' +/- follows it; a FIBA-rule reordering of same-second actions
  broke +/- for 718 players, so it is not used. Within a second, a linked action follows its
  target; a rebound with a mistyped clock (often 00:00) is moved back after its shot, and a
  shot that lands between two free throws of one trip goes before the trip's foul.
- **Lineups** are replayed from the starters; minutes match the box within 5 s for every
  player; +/- matches Genius for 1,295/1,299. The 4 others are in 345923, where Genius'
  +/- follows the mistyped clock that our override corrects.
- **Possessions** (Oliver: an offensive rebound continues a possession) end on a made FG
  (after the and-one FT if the shooter gets one in that dead ball), made last FT, defensive
  rebound, turnover, a held ball won by the other team (`jumpball`) or period end.
  Technical FTs never switch; after unsportsmanlike-type fouls the shooting team keeps the
  ball. Every event carries the possession it happened in; an assist, steal, block or
  foulon carries its target's. Each period has the two teams within one possession of each
  other. Nine switches in the season have no end event in the feed (`inferred`, logged).
  - `pts` is the team's points in its own possessions; technical FTs scored by the defence are
    in `pts_against` (21 points in the season). For team ratings use the box score points
    (or `pts` + the opponent's `pts_against`) over possessions.
  - `fta` leaves out technical FTs, as possession estimates do.
  - Join events to possessions with `events_enriched.possession_no`, not `start_seq..end_seq`:
    an assist entered after a timeout keeps its shot's possession, so a few ranges overlap.
  - 148 possessions are a few seconds at the end of a period with no action
    (`end_type = 'period_end' AND fga + fta + tov = 0`); they are real but you may drop them.
  - `stints.poss_for/poss_against` credit a possession to the five on court at its first live
    action (not the substitutions in the dead ball before it).
- **Overrides**: `overrides/{match_id}.json` fixes feed errors by hand, e.g. actions entered
  with a mistyped clock (`345923`). They are logged in `enrich_issues`.
- **Shots**: each team's basket per half comes from the majority of its shots; the far end is
  rotated 180°. Zones: rim (≤ 1.25 m), paint, midrange, corner 3 (≤ 2.99 m from the
  baseline, 0.90 m strips; stricter than the NBA's 14 ft), above-the-break 3, heave (beyond
  half court). The 2pt/3pt call matches the FIBA line for every located shot. Tip-ins at
  Genius' default spot are `location_imputed`. A fouled missed shot is not a field goal
  attempt in FIBA stats, so `fouled` means and-one. `possession_elapsed_s` can pass 24 s
  after an offensive rebound. `margin_before` is replayed in our order.
  The Genius `fastbreak` qualifier marks fast-break *points* (every tagged shot is a make),
  so use `transition` from `analysis.shot_xpts` for fast-break shooting.

## Analysis tables

`uv run tbl analyze` reads the staged and enriched Parquet and writes
`~/data/tbl/analysis/league=tbl/season=2026-27/*.parquet` (same DuckDB file), `metrics.json`,
and flat CSVs, a FIBA half-court image and a ready workbook (`tbl-2026-27.twb`: 6 interactive
dashboards driven by Takım/Oyuncu/Maç parameters, on Hyper extracts) for Tableau Public in `~/data/tbl/tableau/tbl-2026-27/`
(see [docs/tableau.md](docs/tableau.md)).

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

- **xPTS**: logistic regression on distance (spline), angle, 2/3, end of period, shot family,
  second chance, transition, off a turnover, seconds into the possession and margin; no
  shooter identity. Every shot is predicted by a model fitted without its game (5 folds
  grouped by match). On 2026-27: log loss 0.647 vs 0.659 for zone FG% and 0.687 for a
  constant, xPTS 1.033 per shot vs 1.031 scored. Left out because the feed only knows
  them after the outcome: the `fastbreak` tag, detailed shot sub-types (scorers label
  makes more specifically), assisted, blocked, fouled. Free throws are not in xPTS.
- **Game flow** reproduces FIBA LiveStats' own biggest lead, biggest run, lead changes,
  times tied and time leading for all 112 team-games (a period starting level counts as
  a tie, as LiveStats does).
- **Profiles**: player rates use what happened while the player was on court (lineups on
  every event): USG% = player (FGA + 0.44 FTA + TOV) / the team's on court, so it averages
  20%; AST% = assists / teammates' field goals on court; rebound, steal and block rates use
  on-court opportunities; on/off = team net rating on minus off. Team ratings use counted
  possessions and box points (technical FTs included).
- **Small samples**: 6-7 games per team. Pair ratings and clutch numbers come with their
  possession counts and `poe_se` gives the noise in points over expected; filter on
  them (e.g. pairs with 100+ possessions) rather than reading single numbers.
