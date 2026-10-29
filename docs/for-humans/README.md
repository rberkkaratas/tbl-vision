# tbl-vision for humans

These docs explain what the pipeline does, why it does it that way, and how far
you can trust each number. If you are an AI agent about to change the code,
[../for-ai](../for-ai/README.md) is shorter and more useful.

## Reading order

1. **[methods.md](methods.md)**: how two feeds become possessions, lineups, shot zones
   and models, and how the pipeline decides which numbers are settled. Start here if
   you care about basketball analytics.
2. **[dashboards.md](dashboards.md)**: the nine Tableau pages, how the workbook is
   generated in code, and how to publish it.
3. **[running.md](running.md)**: setup, commands, where data goes, how syncing works,
   troubleshooting.
4. **[data.md](data.md)**: the reference for every table and column convention.

## The project in five paragraphs

The Turkish Basketball League publishes its data in two places. The federation (TBF)
has the fixture list, officials, video links and persistent player ids. FIBA
LiveStats, run by Genius Sports, has the play-by-play, shot locations and box score.
Neither is enough on its own: TBF's play-by-play is partial, and Genius numbers players
per game. tbl-vision ties every Genius player to a TBF id and checks the two sources
against each other.

From the play-by-play it rebuilds every game: the canonical order of events, who was on
court for each one, where each possession started and ended, and where each shot was
taken in metres. Those rebuilt games reproduce the official numbers. Every player's
minutes match the box score within 5 seconds, and game flow (biggest lead, runs, lead
changes) matches FIBA LiveStats' own figures.

On top of that it fits an expected-points model for shots and builds team and player
profiles. It measures how many games each stat needs before it reflects skill rather
than luck. Three-point attempt rate settles in about one game; three-point percentage
needs more than two seasons. Player numbers are blended with last season and the league
average accordingly, and every number on the dashboard carries a mark for how settled
it is.

For a coach, the result is a Tableau dashboard built around one team. The opponent page
projects the next game and picks out the matchups that stand out, weighing each by how
reliable its data is. It describes the opponent's style, players and rotation. It
describes and does not prescribe: with eight games of data, verdicts like "weak
defence" are more than the numbers can carry.

Everything is reproducible from the raw responses, which are stored once and never
overwritten. The data itself is not in this repository and is not redistributed.

## Glossary

| Term | Meaning |
|---|---|
| possession | one team's turn with the ball, ended by a score, a defensive rebound, a turnover or the period's end (an offensive rebound continues it) |
| ORtg / DRtg / net | points scored / allowed per 100 possessions, and the difference |
| adjusted rating | rating corrected for opponent strength and home court |
| pace | possessions per 40 minutes |
| four factors | eFG%, turnover %, offensive rebound %, free-throw rate: the four things that decide possessions |
| eFG% | field-goal percentage with a three counted as 1.5 makes |
| TS% | shooting percentage that also counts free throws |
| USG% | share of team plays a player ends while on court |
| xPTS | points an average shooter would score from a shot like this one |
| POE | points over expected: points scored minus xPTS |
| stabilization point | number of attempts or games after which a rate is half skill, half luck |
| padded / blended | a rate combined with last season and the league average, weighted by sample size |
| ICC | share of a stat's game-to-game variance that is a real difference between teams |
| ● ◐ ○ | settled, partly settled, mostly luck after the current number of games |
| stint | a stretch of play with the same five players on court |
| clutch | the last 5 minutes of the fourth quarter or overtime, within 5 points |
