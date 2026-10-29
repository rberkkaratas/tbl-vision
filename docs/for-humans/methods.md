# Methods

How the pipeline turns two raw feeds into numbers you can lean on, and how far you
can lean on each one. The tables themselves are listed in [data.md](data.md).

Numbers marked "2026-27" were measured on the first eight weeks of the season (64
games). Counts in the enrichment rules come from an earlier snapshot of the same season;
`tbl build` prints the current ones.

## 1. Two sources, one id system

TBF is canonical for ids, names, officials and video links; FIBA LiveStats (Genius)
is canonical for the box score, play-by-play and shot locations. Genius numbers
players per game (`pno`), so every Genius player is tied to a TBF person id:

1. by (team, jersey), which is unique within a team in every game;
2. where TBF has no jersey (early 2025-26 games) or it differs, by name within the
   team: same surname after ASCII folding ("I. Pinarer" = "İbrahim Pınarer"), first
   initial to break ties, and only if exactly one TBF player fits;
3. a player TBF leaves out of one box score gets his season id if his name is unique
   in the season, otherwise a stable negative id, so lineups and box scores stay whole.

`tbl build` stops if a blocking check fails: a 1:1 mapping of everyone who played,
mapped names sharing a word, points adding up, per-period points summing to the
final, shots equal to FGA, TBF's game flow being a subset of Genius' play-by-play, and
no source field we have never seen (schema drift). Fourteen box-score stats per
player are recounted from the play-by-play and must agree.

## 2. Enrichment

- **Order.** Game time first. Inside one clock second, the score Genius stamped on
  each action (it includes the action's own points), then the action number. The stamp
  shows where a late entry belongs, and Genius' +/- follows it. A FIBA-rule reordering
  of same-second actions broke +/- for 718 players, so it is not used. Within a second, a
  linked action follows its target; a rebound with a mistyped clock (often 00:00) is
  moved back after its shot, and a shot that lands between two free throws of one trip
  goes before the trip's foul.
- **Lineups** are replayed from the starters. Player minutes match the box score within
  5 seconds in every player-game of 2025-26 and 2026-27 (8,521). +/- from the
  lineups matches the official number in 8,459 of them; the rest are listed as notes.
- **Possessions** follow Dean Oliver: an offensive rebound continues a possession. A
  possession ends on a made field goal (after the and-one free throw if the shooter gets
  one in that dead ball), a made last free throw, a defensive rebound, a turnover, a held
  ball won by the other team, or the end of the period. Technical free throws never switch
  possession; after unsportsmanlike-type fouls the shooting team keeps the ball. Every
  event carries the possession it happened in; an assist, steal, block or foul-on carries
  its target's. In every period the two teams are within one possession of each other.
  A few switches have no end event in the feed; they are marked `inferred` and logged.
  - `pts` is the team's points in its own possessions; technical free throws scored by
    the defence are in `pts_against`. For team ratings use box-score points over
    possessions.
  - `fta` leaves out technical free throws, as possession estimates do.
  - Join events to possessions with `events_enriched.possession_no`, not
    `start_seq..end_seq`: an assist entered after a timeout keeps its shot's possession,
    so a few ranges overlap.
  - Some possessions are a few seconds at the end of a period with no action
    (`end_type = 'period_end' AND fga + fta + tov = 0`); they are real but you may drop
    them.
  - `stints.poss_for/poss_against` credit a possession to the five on court at its first
    live action, not to the substitutions in the dead ball before it.
- **Overrides.** `overrides/{match_id}.json` fixes feed errors by hand, for example
  actions entered with a mistyped clock (`345923`). Every applied override is logged in
  `enrich_issues`.
- **Shots.** Each team's basket per half comes from the majority of its shots, and the far
  end is rotated 180°. Zones: rim (≤ 1.25 m), paint, mid-range, corner three (≤ 2.99 m from
  the baseline, 0.90 m strips; stricter than the NBA's 14 ft), above-the-break three and
  heave (beyond half court). The 2/3 call matches the FIBA line for every located shot.
  Tip-ins at Genius' default spot are `location_imputed`. A fouled missed shot is not a
  field goal attempt in FIBA stats, so `fouled` means and-one. `margin_before` is replayed
  in our order. Genius' `fastbreak` qualifier marks fast-break *points* (every tagged shot
  is a make), so fast-break shooting uses `transition` from `shot_xpts`.
- **Game flow** reproduces FIBA LiveStats' own biggest lead, biggest run, lead changes,
  times tied and time leading for all 128 team-games of 2026-27 (a period that starts
  level counts as a tie, as LiveStats does).

## 3. Expected points (xPTS)

A logistic regression on what the shooter could see before the shot: distance (spline),
angle, 2 or 3, the last seconds of a period, shot family (dunk, layup, tip-in, hook,
jumper), second chance, transition, off a turnover, seconds into the possession and the
score margin. The shooter's identity is left out on purpose: xPTS describes the shot, and
points minus xPTS (POE) is what the shooter added. Features the feed only knows after the
outcome are left out too: the `fastbreak` tag, detailed shot sub-types (scorers label
makes more specifically), assisted, blocked, fouled. Free throws are not in xPTS.

Every shot is predicted by a model fitted without its game (5 folds grouped by match),
trained on last season's 38,003 shots plus this season's other folds.

| 2026-27, 8,162 shots | Log loss |
|---|---|
| Constant | 0.6874 |
| Zone FG% only | 0.659 |
| xPTS (pooled) | 0.6468 |
| Last season's model, frozen | 0.6467 |
| This season only | 0.6486 |

The frozen model's calibration slope is 1.006 and intercept +0.006: the model does not
drift between seasons, and last season's data helps.

## 4. Profiles

Team ratings use counted possessions and box-score points (technical free throws
included). Four factors follow Oliver: eFG%, TOV% (turnovers per possession), ORB% and
DRB%, FT rate (FTM / FGA); team rebounds and team turnovers count.

Player rates use what happened while the player was on court:

- USG% = (FGA + 0.44 FTA + TOV) / the team's same total on court, so it averages 20%;
- AST% = assists / teammates' field goals on court;
- rebound, steal and block rates use on-court opportunities;
- on/off = team net rating with the player on court minus off.

## 5. How much of a number is skill

Every player rate is a mean over some unit (FT% over free throws, USG% over team plays
on court, POE over shots). For each rate, odd and even games of the same player give the
sampling noise σ², and the season's spread gives the true spread τ² (method of moments).
`k = σ² / τ²` units make a rate half skill, half luck.

On 2025-26: FT% 17 attempts, 3P% about 270, USG% 56 team plays, STL% 520 opponent
possessions, POE 290 shots, on/off about 710 possessions. Close to published NBA
estimates. In games, for a player on about 25 minutes: 3PA rate 0.6, usage 1.1, assist
rate 1.1, shot quality 2.9, FT% 8.1, TS% 22.7, shot-making 39.2, on/off 40, 3P% 76.3.

**Padded rates** combine this season, last season and the league mean:

```
(n·p + d·n_prev·p_prev + k·μ) / (n + d·n_prev + k)
```

Last season comes from our own play-by-play, or from TBF's career totals for players who
were in another league (at that league's own weight). The weight `d` is tuned per group
by predicting the rest of 2025-26 from its first games with 2024-25 totals: shooting
about 1.0, role 0.45, per-minute production 0.3. After 4 games, padding cuts the error
of the raw rate by 7-62%, last season cuts it by a further 0.5-22% for players who have
one, and the 90% intervals cover about 89% of outcomes.

**Team stats** get the same treatment with an intraclass correlation (ICC): the share of
a single game's variance that is between teams rather than game to game, fitted on last
season. After n games a season average is `n·ICC / (1 + (n-1)·ICC)` reliable
(Spearman-Brown), and with the current number of games each stat gets a tier that the
dashboard shows as a mark:

| Tier | Mark | Reliability now |
|---|---|---|
| `oturdu` (settled) | ● | ≥ 0.45 |
| `kısmen` (partly) | ◐ | 0.25-0.45 |
| `gürültü` (noise) | ○ | < 0.25 |

At eight games, style is settled (net rating, mid-range share, transition share, 3PA
rate, pace), while defensive outcomes such as the three-point percentage a team allows
are still noise.

## 6. Matchups

- **Ratings.** Every team-game's points per possession is fitted as league average +
  the team's offence + the opponent's defence ± home court, weighted by possessions,
  with a ridge penalty (λ = 1000): each rating starts at the league average and moves
  away only as games pull it.
- **Projection.** Expected pace = pace_A × pace_B / league pace; each side's points per
  possession from the ratings; win probability = Φ(margin / σ), where σ is the
  walk-forward error SD of the season before (12.2 points). The 80% range is
  margin ± 1.28 σ.
- **Backtest.** Each game projected only from the games before it. 2025-26: 262 games,
  winner right 70%, mean absolute error 9.5 points, Brier 0.202 (home court alone:
  0.230). 2026-27 so far: 37 games, 57%, 10.1 points, Brier 0.241 (0.248). Early in a
  season the ratings know little; the dashboard shows both lines under the game card.
- **Four factors.** Expected = attacker's value × what the defence allows / league. Impact
  = weight × (expected - league) in points per 100 possessions, with weights fitted on
  the season's games (eFG +1.48, TOV -1.23, ORB +0.61, FT rate +0.26 per percentage
  point). A single game's four factors are mostly noise: the matchup expectation beats a
  team's own average by only about 1% in mean absolute error. So the "standout
  matchups" are season tendencies, ranked by |impact| × tier weight (● 1, ◐ 0.6, ○ 0.25).
- **Home court** this season: about 2.3 points a game.

## 7. Wording

Text that a coach reads describes and never prescribes. Cards say what the numbers show
("Ormanspor: 0.34 · 1st of 17", "tendency +3.7 per 100, toward Ormanspor", "bottom third
of the league") and leave out verdicts such as "weak defence" or "stop this player".
Player tags describe extremes ("uses the ball a lot", top 20% of qualified players), and
every card carries its data strength.

## 8. Limits

- Eight games per team is a small sample. Pair ratings, lineup nets, clutch numbers and
  quarter splits come with their possession counts or standard errors; filter on them
  rather than reading single numbers.
- Nothing about injuries, transfers mid-week, or practice is in the data.
- xPTS knows where and how a shot was taken, not who guarded it.
- The feed has errors. The ones found so far are fixed in `overrides/` and logged;
  others may remain.
