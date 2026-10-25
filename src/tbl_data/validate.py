"""Cross-source checks on staged tables. Each check returns failing rows (empty = pass)."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from .schemas import BOX_STATS


@dataclass
class Check:
    name: str
    description: str
    checked: int
    failures: pl.DataFrame
    blocking: bool = True  # False: known, explained difference that is reported only

    @property
    def ok(self) -> bool:
        return self.failures.height == 0


def run_all(t: dict[str, pl.DataFrame], drift: pl.DataFrame, unmapped: list[dict]) -> list[Check]:
    return [
        box_tbf_vs_genius(t),
        minutes_tbf_vs_genius(t),
        player_mapping(t, unmapped),
        mapped_names(t),
        tbf_quirks(t),
        team_points(t),
        forfeits(t),
        period_scores(t),
        shots_vs_fga(t),
        tbf_shots_vs_genius(t),
        events_vs_box(t),
        tbf_events_subset(t),
        career_points(t),
        Check("schema_drift", "no unseen source fields", drift.height, drift),
    ]


COUNTING = tuple(c for c in BOX_STATS if c != "seconds")


def _box_join(t) -> pl.DataFrame:
    g = t["match_players"].select("match_id", "player_id", *BOX_STATS)
    b = t["tbf_box"].select("match_id", "player_id", *BOX_STATS)
    return g.join(b, on=["match_id", "player_id"], suffix="_tbf")


def box_tbf_vs_genius(t) -> Check:
    """Every counting stat of every player equal in Genius and TBF. Genius is kept: TBF's box
    is sometimes stale (2025-26 week 1: points missing for whole teams) or lacks fouls drawn /
    shots blocked, and a basket is occasionally credited to a teammate. A wrong (team, jersey)
    mapping shows up in mapped_names instead."""
    j = _box_join(t)
    diffs = [
        j.filter(pl.col(c).ne_missing(pl.col(f"{c}_tbf")))
        .select("match_id", "player_id", pl.lit(c).alias("stat"),
                pl.col(c).cast(pl.Int64).alias("genius"), pl.col(f"{c}_tbf").cast(pl.Int64).alias("tbf"))
        for c in COUNTING
    ]
    return Check("box_tbf_vs_genius", f"{len(COUNTING)} counting stats x player equal in both sources (informational)",
                 j.height * len(COUNTING), pl.concat(diffs), blocking=False)


def minutes_tbf_vs_genius(t) -> Check:
    """Playing time within 1 s (sources round differently). Larger gaps are TBF
    crediting time to the wrong teammate (team totals still agree); Genius is kept."""
    j = _box_join(t).with_columns(diff=pl.col("seconds") - pl.col("seconds_tbf"))
    return Check("minutes_tbf_vs_genius", "player seconds within 1 s of TBF (informational)", j.height,
                 j.filter(pl.col("diff").abs() > 1).select("match_id", "player_id", "seconds", "seconds_tbf", "diff"),
                 blocking=False)


def player_mapping(t, unmapped) -> Check:
    """Every Genius player who played has a TBF id, every TBF box row of a player who played a Genius row
    (TBF also lists unused players, often without a jersey; Genius may not)."""
    g, b = t["match_players"], t["tbf_box"]
    only_tbf = b.filter(pl.col("seconds") > 0).join(g, on=["match_id", "player_id"], how="anti").select(
        "match_id", "team_season_id", "jersey", "name", pl.lit("tbf_only").alias("side"))
    only_g = pl.DataFrame(unmapped, schema={"match_id": pl.Int32, "team_season_id": pl.Int32,
                                            "jersey": pl.String, "name": pl.String}
                          ).with_columns(pl.lit("genius_only").alias("side"))
    return Check("player_mapping", "Genius (team, jersey) <-> TBF person id is 1:1", g.height + b.height,
                 pl.concat([only_tbf, only_g]))


def mapped_names(t) -> Check:
    """Every (team, jersey) match joins two names that share a word (ASCII-folded, so İ/ı and
    accents do not matter): a wrong jersey would join two different players."""
    from .build import _fold

    j = t["match_players"].select("match_id", "player_id", "jersey", "name").join(
        t["tbf_box"].select("match_id", "player_id", pl.col("name").alias("name_tbf")), on=["match_id", "player_id"])
    share = pl.struct("name", "name_tbf").map_elements(
        lambda r: bool(set(_fold(r["name"] or "")) & set(_fold(r["name_tbf"] or ""))), return_dtype=pl.Boolean)
    return Check("mapped_names", "Genius and TBF names of each mapped player share a word", j.height,
                 j.filter(~share))


def tbf_quirks(t) -> Check:
    """Our reading of TBF fields: fgm = 2P made + 3P made, reb = oreb + dreb."""
    b = t["tbf_box"]
    bad = b.filter((pl.col("fgm") != pl.col("fg2m") + pl.col("fg3m"))
                   | (pl.col("fga") != pl.col("fg2a") + pl.col("fg3a"))
                   | (pl.col("reb") != pl.col("oreb") + pl.col("dreb"))
                   | (pl.col("pts") != 2 * pl.col("fg2m") + 3 * pl.col("fg3m") + pl.col("ftm")))
    return Check("tbf_quirks", "TBF box fields add up under our field mapping", b.height,
                 bad.select("match_id", "player_id", "pts", "fgm", "fg2m", "fg3m", "ftm"))


def team_points(t) -> Check:
    """Sum of player points = Genius team points = TBF final score (forfeits: see forfeits)."""
    m = t["matches"].filter(~pl.col("forfeit"))
    p = t["match_players"].join(m.select("match_id"), on="match_id").group_by("match_id", "team_season_id").agg(
        player_pts=pl.col("pts").sum())
    final = pl.concat([
        m.select("match_id", pl.col("home_team_id").alias("team_season_id"), pl.col("home_score").alias("final")),
        m.select("match_id", pl.col("away_team_id").alias("team_season_id"), pl.col("away_score").alias("final")),
    ])
    j = (t["match_teams"].join(m.select("match_id"), on="match_id")
         .select("match_id", "team_season_id", pl.col("pts").alias("team_pts"))
         .join(p, on=["match_id", "team_season_id"], how="full", coalesce=True)
         .join(final, on=["match_id", "team_season_id"], how="full", coalesce=True))
    bad = j.filter((pl.col("team_pts") != pl.col("player_pts")) | (pl.col("team_pts") != pl.col("final"))
                   | pl.any_horizontal(pl.col("team_pts", "player_pts", "final").is_null()))
    return Check("team_points", "player sum = team total = TBF final score", j.height, bad)


def forfeits(t) -> Check:
    """Games played but officially scored 20-0 (e.g. an ineligible player). Analysis uses the
    played score; matches keeps the official one."""
    m = t["matches"]
    return Check("forfeits", "games scored 20-0 officially but played (informational)", m.height,
                 m.filter(pl.col("forfeit")).select("match_id", "week", "home_team_id", "away_team_id",
                                                    "home_score", "away_score"), blocking=False)


def period_scores(t) -> Check:
    """Points per period rebuilt from the play-by-play sum to the final score."""
    s = t["period_scores"].group_by("match_id", "team_season_id").agg(pbp_pts=pl.col("pts").sum())
    j = t["match_teams"].select("match_id", "team_season_id", "pts").join(s, on=["match_id", "team_season_id"], how="left")
    return Check("period_scores", "sum of per-period points (from pbp) = final", j.height,
                 j.filter(pl.col("pts").ne_missing(pl.col("pbp_pts"))))


def shots_vs_fga(t) -> Check:
    """Genius shot list has exactly one entry per field goal attempt."""
    s = t["shots"].group_by("match_id", "team_season_id").agg(
        shots=pl.len(), made=pl.col("made").sum(), threes=(pl.col("shot_type") == "3pt").sum())
    j = t["match_teams"].select("match_id", "team_season_id", "fga", "fgm", "fg3a").join(
        s, on=["match_id", "team_season_id"], how="left")
    bad = j.filter((pl.col("shots").ne_missing(pl.col("fga"))) | (pl.col("made").ne_missing(pl.col("fgm")))
                   | (pl.col("threes").ne_missing(pl.col("fg3a"))))
    return Check("shots_vs_fga", "shots = FGA, made = FGM, 3pt shots = 3PA (per team)", j.height, bad)


def tbf_shots_vs_genius(t) -> Check:
    """TBF shot chart is a copy of Genius; report matches where counts differ."""
    g = t["shots"].group_by("match_id").agg(genius=pl.len())
    b = t["tbf_shots"].group_by("match_id").agg(tbf=pl.len())
    j = g.join(b, on="match_id", how="full", coalesce=True).with_columns(
        pl.col("genius", "tbf").fill_null(0)).with_columns(diff=pl.col("genius") - pl.col("tbf"))
    return Check("tbf_shots_vs_genius", "TBF shot chart count = Genius shots (informational)", j.height,
                 j.filter(pl.col("diff") != 0).sort("diff", descending=True), blocking=False)


def events_vs_box(t) -> Check:
    """Each player's box score recounted from play-by-play events.

    'offensivedeadball' rebounds (after a missed non-final free throw, ball not live) are
    not rebounds in FIBA stats and are excluded; pf counts every foul event incl. technicals.
    """
    e, a, s = t["events"].filter(pl.col("player_id").is_not_null()), pl.col("action_type"), pl.col("sub_type")
    made = pl.col("success")
    counts = e.group_by("match_id", "player_id").agg(
        fg2m=((a == "2pt") & made).sum(), fg2a=(a == "2pt").sum(),
        fg3m=((a == "3pt") & made).sum(), fg3a=(a == "3pt").sum(),
        ftm=((a == "freethrow") & made).sum(), fta=(a == "freethrow").sum(),
        oreb=((a == "rebound") & (s == "offensive")).sum(), dreb=((a == "rebound") & (s == "defensive")).sum(),
        ast=(a == "assist").sum(), stl=(a == "steal").sum(), blk=(a == "block").sum(), tov=(a == "turnover").sum(),
        pf=(a == "foul").sum(), fouls_drawn=(a == "foulon").sum(),
    )
    cols = [c for c in counts.columns if c not in ("match_id", "player_id")]
    j = t["match_players"].select("match_id", "player_id", *cols).join(
        counts, on=["match_id", "player_id"], how="left", suffix="_pbp").with_columns(
        pl.col(f"{c}_pbp").fill_null(0) for c in cols)
    diffs = [j.filter(pl.col(c) != pl.col(f"{c}_pbp")).select(
        "match_id", "player_id", pl.lit(c).alias("stat"), pl.col(c).cast(pl.Int64).alias("box"),
        pl.col(f"{c}_pbp").cast(pl.Int64).alias("pbp")) for c in cols]
    return Check("events_vs_box", f"{len(cols)} box stats per player recounted from pbp", j.height * len(cols),
                 pl.concat(diffs))


# entries the scorer can add or renumber after TBF's copy was taken (304218, 2025-26)
TBF_ADMIN_EVENTS = ("Maç Sonu", "Head Coach Challenge")


def tbf_events_subset(t) -> Check:
    """Every TBF game-flow action number exists in the Genius play-by-play."""
    j = t["tbf_events"].join(t["events"].select("match_id", "action_number"), on=["match_id", "action_number"], how="anti"
                             ).filter(~pl.col("text").is_in(TBF_ADMIN_EVENTS))
    return Check("tbf_events_subset", "TBF game-flow actions are a subset of Genius pbp", t["tbf_events"].height,
                 j.select("match_id", "action_number", "text"))


def career_points(t) -> Check:
    """TBF career rows: pts = 2*fg2m + 3*fg3m + ftm (proves fieldGoalsMade = 2P made).

    Legacy points-only rows (shooting_missing) are skipped; a few pre-2017 club rows
    still differ by a few points at the source.
    """
    c = t["player_career"].filter(~pl.col("shooting_missing"))
    bad = c.filter(pl.col("pts") != 2 * pl.col("fg2m") + 3 * pl.col("fg3m") + pl.col("ftm"))
    return Check("career_points", "career pts = 2*2PM + 3*3PM + FTM (informational)", c.height,
                 bad.select("player_id", "career_season", "competition", "pts", "fg2m", "fg3m", "ftm"), blocking=False)


def report(checks: list[Check]) -> pl.DataFrame:
    return pl.DataFrame([{"check": c.name, "ok": c.ok, "blocking": c.blocking, "checked": c.checked,
                          "failures": c.failures.height, "description": c.description} for c in checks])


# -- enriched layer -------------------------------------------------------------------
def run_enriched(s: dict[str, pl.DataFrame], e: dict[str, pl.DataFrame]) -> list[Check]:
    return [
        stint_seconds(s, e),
        stint_plus_minus(s, e),
        stint_possessions(e),
        possession_points(s, e),
        possession_attribution(e),
        possession_balance(s, e),
        possessions_vs_estimate(s, e),
        ft_trips_vs_box(s, e),
        ft_trip_fouls(e),
        attack_ends(e),
        attack_switch(e),
        shot_zones(e),
        Check("enrich_issues", "order/lineup/possession anomalies logged in enrich_issues (informational)",
              e["events_enriched"].height, e["enrich_issues"].filter(pl.col("source") != "override"), blocking=False),
    ]


def _player_stints(e) -> pl.DataFrame:
    return (e["stints"].explode("lineup").rename({"lineup": "player_id"})
            .group_by("match_id", "team_season_id", "player_id")
            .agg(stint_s=pl.col("seconds").sum(), stint_pm=(pl.col("pts_for") - pl.col("pts_against")).sum()))


def stint_seconds(s, e) -> Check:
    """Minutes rebuilt from substitutions within 5 s of the official box score."""
    j = s["match_players"].select("match_id", "team_season_id", "player_id", "seconds").join(
        _player_stints(e), on=["match_id", "team_season_id", "player_id"], how="left").with_columns(
        pl.col("stint_s").fill_null(0.0), diff=(pl.col("stint_s").fill_null(0.0) - pl.col("seconds")))
    return Check("stint_seconds", "player seconds from lineups within 5 s of box", j.height,
                 j.filter(pl.col("diff").abs() > 5))


def stint_plus_minus(s, e) -> Check:
    """+/- rebuilt from lineups vs Genius. Known residual: 345923, where our override fixes
    a mistyped clock that Genius' own +/- still follows (4 players)."""
    j = s["match_players"].select("match_id", "team_season_id", "player_id", "plus_minus").join(
        _player_stints(e), on=["match_id", "team_season_id", "player_id"], how="left").with_columns(
        pl.col("stint_pm").fill_null(0))
    return Check("stint_plus_minus", "player +/- from lineups = official (informational)", j.height,
                 j.filter(pl.col("stint_pm") != pl.col("plus_minus")), blocking=False)


def stint_possessions(e) -> Check:
    """Every possession is credited to exactly one stint of each team."""
    p = e["possessions"]
    n = p.group_by("match_id", "team_season_id").agg(n=pl.len())
    tot = p.group_by("match_id").agg(total=pl.len())
    st = e["stints"].group_by("match_id", "team_season_id").agg(pl.col("poss_for").sum(), pl.col("poss_against").sum())
    j = st.join(n, on=["match_id", "team_season_id"], how="left").join(tot, on="match_id").with_columns(pl.col("n").fill_null(0))
    return Check("stint_possessions", "stint poss_for/against add up to the team's/opponent's possessions", j.height,
                 j.filter((pl.col("poss_for") != pl.col("n")) | (pl.col("poss_against") != pl.col("total") - pl.col("n"))))


def possession_points(s, e) -> Check:
    """Possession points add up to each team's final score, and the only points scored by
    the team without the ball are technical free throws."""
    p = e["possessions"]
    own = p.group_by("match_id", "team_season_id").agg(own=pl.col("pts").sum())
    # points a team scores during the other team's possession (technical FTs)
    other = p.join(s["match_teams"].select("match_id", pl.col("team_season_id").alias("scorer")), on="match_id") \
        .filter(pl.col("scorer") != pl.col("team_season_id")) \
        .group_by("match_id", "scorer").agg(other=pl.col("pts_against").sum()) \
        .rename({"scorer": "team_season_id"})
    j = s["match_teams"].select("match_id", "team_season_id", "pts").join(own, on=["match_id", "team_season_id"], how="left") \
        .join(other, on=["match_id", "team_season_id"], how="left").with_columns(
        total=pl.col("own").fill_null(0) + pl.col("other").fill_null(0))
    ev = e["events_enriched"].filter(pl.col("action_type").is_in(["2pt", "3pt", "freethrow"]) & pl.col("success")
                                     & (pl.col("team_season_id") != pl.col("offense_team_id")))
    tech = e["ft_trips"].filter(pl.col("trip_type") == "technical").select("match_id", ft_trip_no="trip_no", tech=pl.lit(True))
    bad_ev = ev.join(tech, on=["match_id", "ft_trip_no"], how="left").filter(pl.col("tech").is_null()) \
        .select("match_id", "action_number", "action_type", "team_season_id", "offense_team_id")
    bad = pl.concat([j.filter(pl.col("total") != pl.col("pts")).select("match_id", "team_season_id")
                     .with_columns(action_number=pl.lit(None, pl.Int32), problem=pl.lit("final score")),
                     bad_ev.select("match_id", "team_season_id", "action_number", problem=pl.lit("points on defense"))])
    return Check("possession_points", "possession points = final score; defense scores only technical FTs",
                 j.height + ev.height, bad)


DESCRIBES = ["assist", "steal", "block", "foulon"]


def possession_attribution(e) -> Check:
    """Offensive actions are by the team with the ball, defensive rebounds by the other one,
    and an assist/steal/block/foulon sits in the possession of the action it describes."""
    ev = e["events_enriched"]
    a, team, off = pl.col("action_type"), pl.col("team_season_id"), pl.col("offense_team_id")
    wrong = ev.filter(team.is_not_null() & (
        (a.is_in(["2pt", "3pt", "turnover"]) & (team != off))
        | ((a == "rebound") & (pl.col("sub_type") == "offensive") & (team != off))
        | ((a == "rebound") & (pl.col("sub_type") == "defensive") & (team == off))))
    tgt = ev.select("match_id", previous_action="action_number", tgt_poss="possession_no", tgt_period="period_ord")
    d = ev.filter(a.is_in(DESCRIBES) & pl.col("previous_action").is_not_null()).join(
        tgt, on=["match_id", "previous_action"], how="inner").filter(pl.col("period_ord") == pl.col("tgt_period"))
    bad = pl.concat([wrong.select("match_id", "action_number", "action_type"),
                     d.filter(pl.col("possession_no") != pl.col("tgt_poss")).select("match_id", "action_number", "action_type")])
    return Check("possession_attribution", "events agree with who has the ball; describing actions share their target's possession",
                 ev.height, bad)


def possession_balance(s, e) -> Check:
    """Possessions alternate, so in each period the two teams' counts differ by at most one,
    plus one per logged possession_switch_without_end (a missing turnover or rebound in the feed)."""
    p = e["possessions"].join(s["match_teams"].select("match_id", "team_season_id", "is_home"),
                              on=["match_id", "team_season_id"])
    j = p.group_by("match_id", "period_ord").agg(home=pl.col("is_home").sum(), away=(~pl.col("is_home")).sum())
    gaps = (e["enrich_issues"].filter(pl.col("issue") == "possession_switch_without_end")
            .join(e["events_enriched"].select("match_id", "action_number", "period_ord"), on=["match_id", "action_number"])
            .group_by("match_id", "period_ord").agg(feed_gaps=pl.len()))
    j = j.join(gaps, on=["match_id", "period_ord"], how="left").with_columns(pl.col("feed_gaps").fill_null(0))
    return Check("possession_balance", "|home - away possessions| <= 1 (+ feed gaps) in every period", j.height,
                 j.filter((pl.col("home").cast(pl.Int64) - pl.col("away").cast(pl.Int64)).abs() > 1 + pl.col("feed_gaps")))


def possessions_vs_estimate(s, e) -> Check:
    """Counted possessions vs the box-score estimate FGA - OREB + TOV + 0.44 FTA."""
    n = e["possessions"].group_by("match_id", "team_season_id").agg(n=pl.len())
    j = s["match_teams"].join(n, on=["match_id", "team_season_id"]).with_columns(
        est=pl.col("fga") - pl.col("oreb") + pl.col("tov") + 0.44 * pl.col("fta")).with_columns(
        diff=(pl.col("n") - pl.col("est")).round(2))
    return Check("possessions_vs_estimate", "counted possessions within 4 of the box estimate (informational)",
                 j.height, j.filter(pl.col("diff").abs() > 4).select("match_id", "team_season_id", "n", "est", "diff"),
                 blocking=False)


def ft_trips_vs_box(s, e) -> Check:
    """Every free throw belongs to exactly one complete trip; trips add up to FTA/FTM."""
    t = e["ft_trips"].group_by("match_id", "team_season_id").agg(
        fta_t=pl.col("attempts").sum(), ftm_t=pl.col("made").sum(), incomplete=(~pl.col("complete")).sum())
    j = s["match_teams"].select("match_id", "team_season_id", "fta", "ftm").join(
        t, on=["match_id", "team_season_id"], how="left").with_columns(pl.col("fta_t", "ftm_t", "incomplete").fill_null(0))
    return Check("ft_trips_vs_box", "trip attempts/makes = FTA/FTM, all trips complete", j.height,
                 j.filter((pl.col("fta_t") != pl.col("fta")) | (pl.col("ftm_t") != pl.col("ftm")) | (pl.col("incomplete") > 0)))


def ft_trip_fouls(e) -> Check:
    """Trips whose causing foul could not be found (a feed error, e.g. foul on the wrong team)."""
    t = e["ft_trips"]
    return Check("ft_trip_fouls", "every FT trip linked to the foul that awarded it (informational)", t.height,
                 t.filter(pl.col("foul_action").is_null()).select("match_id", "trip_no", "team_season_id", "shooter_id",
                                                                   "n_awarded", "trip_type"), blocking=False)


def _ends(e, by: str) -> pl.DataFrame:
    """Majority attacked end per team and period/half, in raw feed coordinates."""
    sh = e["shots_enriched"].filter(~pl.col("location_imputed")).with_columns(
        half=pl.when(pl.col("period_ord") <= 2).then(1).otherwise(2), high=pl.col("x") > 50)
    return sh.group_by("match_id", "team_season_id", by).agg(high=pl.col("high").mean() > 0.5)


def attack_ends(e) -> Check:
    """In every period the two teams shoot at opposite baskets, and every shot is measured from
    the end its team attacked (shots_enriched: almost no heaves in a team's period)."""
    ends = _ends(e, "period_ord")
    by_period = ends.group_by("match_id", "period_ord").agg(n=pl.len(), opposite=pl.col("high").n_unique() == 2)
    heaves = e["shots_enriched"].group_by("match_id", "team_season_id", "period_ord").agg(
        n=pl.len(), heaves=(pl.col("zone") == "heave").sum())
    bad = pl.concat([by_period.filter((pl.col("n") == 2) & ~pl.col("opposite")).select("match_id", "period_ord",
                                                                                       what=pl.lit("same end")),
                     heaves.filter((pl.col("n") >= 5) & (pl.col("heaves") > pl.col("n") / 2)).select(
                         "match_id", "period_ord", what=pl.lit("measured from the wrong end"))])
    return Check("attack_ends", "teams attack opposite ends; shots measured from the attacked end", ends.height, bad)


def attack_switch(e) -> Check:
    """Teams switch ends at half-time in the raw feed. A failure is a scorer who mirrored a
    period; shots.attack_ends follows the period's own shots there."""
    ends = _ends(e, "period_ord").with_columns(half=pl.when(pl.col("period_ord") <= 2).then(1).otherwise(2))
    first = ends.filter(pl.col("period_ord") == 1).select("match_id", "team_season_id", pl.col("high").alias("q1"))
    bad = ends.join(first, on=["match_id", "team_season_id"]).filter(
        (pl.col("high") == pl.col("q1")) != (pl.col("half") == 1))
    return Check("attack_switch", "teams keep their end within a half and switch at half-time (informational)",
                 ends.height, bad.select("match_id", "team_season_id", "period_ord"), blocking=False)


ON_LINE_M = 0.10  # pins this close to the 3pt line are ambiguous (Genius snaps corner shots to y = 6.05)
MISPLACED_PIN_RATE = 0.001  # more 2pt/3pt disagreements than this point at our geometry, not the scorer


def shot_zones(e) -> Check:
    """The scorer's 2pt/3pt call agrees with the FIBA line at the recorded spot (corner strips
    0.90 m from the sidelines up to 2.99 m, arc 6.75 m elsewhere), for pins clear of the line.
    A few pins are simply misplaced (304227, 2025-26: two 2pt shots pinned 7 m out); only a
    rate above MISPLACED_PIN_RATE blocks."""
    sh = e["shots_enriched"].filter((pl.col("zone") != "heave") & ~pl.col("location_imputed"))
    side = pl.min_horizontal(pl.col("y_m"), 15 - pl.col("y_m"))
    corner = (pl.col("x_m") <= 2.99) & (side < 0.9)
    beyond = corner | ((pl.col("x_m") > 2.99) & (pl.col("distance_m") > 6.75))
    on_line = pl.when(pl.col("x_m") <= 2.99).then((side - 0.9).abs()).otherwise(
        (pl.col("distance_m") - 6.75).abs()) <= ON_LINE_M
    bad = sh.filter(((pl.col("shot_type") == "3pt") != beyond) & ~on_line)
    return Check("shot_zones", "2pt/3pt call matches the FIBA 3pt line at the shot location", sh.height,
                 bad.select("match_id", "action_number", "shot_type", "zone", "distance_m", "x_m", "y_m"),
                 blocking=bad.height > MISPLACED_PIN_RATE * sh.height)


# -- analysis layer -------------------------------------------------------------------
def run_analysis(s: dict[str, pl.DataFrame], e: dict[str, pl.DataFrame], a: dict[str, pl.DataFrame],
                 metrics: dict) -> list[Check]:
    return [game_flow_vs_genius(s, a), score_timeline_final(s, a), xpts_calibration(a, metrics),
            pairs_consistency(a), assist_pairs_total(e, a), team_profile_totals(s, a), player_on_court_totals(e, a),
            calibration_bins(a), xpts_out_of_time(metrics), stat_reliability(a), split_half(a), prior_backtest(a), player_stable(a)]


def game_flow_vs_genius(s, a) -> Check:
    """Leads, runs, lead changes, ties and time leading rebuilt from our order = FIBA LiveStats' own."""
    g = a["game_flow"].join(s["match_teams"].select(
        "match_id", "team_season_id", g_lead="biggest_lead", g_run="biggest_run", g_lc="lead_changes",
        g_tied="times_tied", g_tl="time_leading_s"), on=["match_id", "team_season_id"])
    bad = g.filter((pl.col("biggest_lead") != pl.col("g_lead")) | (pl.col("biggest_run") != pl.col("g_run"))
                   | (pl.col("lead_changes_game") != pl.col("g_lc"))
                   | ~pl.col("g_tied").is_between(pl.col("times_tied") - pl.col("ties_at_break"), pl.col("times_tied"))
                   | ((pl.col("time_leading_s") - pl.col("g_tl")).abs() > 2))
    return Check("game_flow_vs_genius", "biggest lead/run, lead changes, ties (± level breaks), time leading (2 s) = Genius", g.height,
                 bad.select("match_id", "team_season_id", "biggest_lead", "g_lead", "biggest_run", "g_run",
                            "times_tied", "g_tied", "time_leading_s", "g_tl"))


def score_timeline_final(s, a) -> Check:
    last = a["score_timeline"].sort("seq").group_by("match_id").last()
    j = last.join(s["matches"].filter(~pl.col("forfeit")).select("match_id", "home_score", "away_score"),
                  on="match_id", suffix="_official")
    return Check("score_timeline_final", "score timeline ends on the official final score", j.height,
                 j.filter((pl.col("home_score") != pl.col("home_score_official"))
                          | (pl.col("away_score") != pl.col("away_score_official"))))


def xpts_calibration(a, metrics) -> Check:
    """Out-of-fold xPTS add up to the points scored (within 2%) and beat the zone-FG% baseline."""
    m = metrics["xpts"]
    sh = a["shot_xpts"]
    rows = pl.DataFrame([{"what": "total xPTS vs points", "value": float(sh["xpts"].sum()), "target": float(sh["pts"].sum())},
                         {"what": "log loss vs zone baseline", "value": m["log_loss"], "target": m["log_loss_zone_baseline"]}])
    bad = rows.filter(((pl.col("what") == "total xPTS vs points") & ((pl.col("value") / pl.col("target") - 1).abs() > 0.02))
                      | ((pl.col("what") == "log loss vs zone baseline") & (pl.col("value") >= pl.col("target"))))
    return Check("xpts_calibration", f"xPTS sum within 2% of points; log loss {m['log_loss']} < zone baseline "
                 f"{m['log_loss_zone_baseline']} (out-of-fold: {m['out_of_fold']})", sh.height, bad)


def pairs_consistency(a) -> Check:
    p = a["pairs"]
    bad = p.filter((pl.col("a_without_b_seconds") < -0.5) | (pl.col("b_without_a_seconds") < -0.5)
                   | (pl.col("a_without_b_poss_for") < 0) | (pl.col("b_without_a_poss_for") < 0))
    return Check("pairs_consistency", "together + apart = each player's on-court totals (no negative remainder)",
                 p.height, bad.select("team_season_id", "player_a", "player_b", "seconds"))


def assist_pairs_total(e, a) -> Check:
    n = e["shots_enriched"].filter(pl.col("assisted")).group_by("team_season_id").agg(n=pl.len())
    t = a["assist_pairs"].group_by("team_season_id").agg(t=pl.col("assists").sum())
    j = n.join(t, on="team_season_id", how="full", coalesce=True).fill_null(0)
    return Check("assist_pairs_total", "assist network adds up to assisted shots per team", j.height,
                 j.filter(pl.col("n") != pl.col("t")))


def team_profile_totals(s, a) -> Check:
    """Team profiles add up to the box score, and the league row balances (ORtg = DRtg)."""
    t = a["team_profiles"]
    box = s["match_teams"].group_by("team_season_id").agg(box_pts=pl.col("pts").sum(), box_games=pl.len())
    j = t.filter(~pl.col("is_league")).join(box, on="team_season_id", how="full", coalesce=True)
    lg = t.filter(pl.col("is_league"))
    bad = pl.concat([
        j.filter((pl.col("pts") != pl.col("box_pts")) | (pl.col("games") != pl.col("box_games")))
        .select("team_season_id", problem=pl.lit("points/games differ from box")),
        lg.filter(pl.col("ortg") != pl.col("drtg")).select("team_season_id", problem=pl.lit("league ORtg != DRtg")),
    ])
    return Check("team_profile_totals", "team points/games = box; league ORtg = DRtg", j.height + 1, bad)


def player_on_court_totals(e, a) -> Check:
    """Each team possession is on every on-court player's record (five; Genius' own feed keeps
    six on court for a half in 304241, 2025-26, and so do we); usage averages 20% on court."""
    p = a["player_profiles"]
    n = e["stints"].group_by("team_season_id").agg(n=(pl.col("lineup").list.len() * pl.col("poss_for")).sum())
    j = p.group_by("team_season_id").agg(
        on=pl.col("on_poss_for").sum(),
        usg=(pl.col("usg") * pl.col("on_team_plays")).sum() / pl.col("on_team_plays").sum()).join(n, on="team_season_id")
    bad = j.filter((pl.col("on") != pl.col("n")) | ~pl.col("usg").is_between(0.19, 0.21))
    return Check("player_on_court_totals", "players' on-court possessions = team's x lineup size; usage averages 19-21%",
                 j.height, bad)


def calibration_bins(a) -> Check:
    """Ten bins of predicted FG% per model, each with its own predicted and observed FG%, rising."""
    c = a["xpts_calibration"].sort("method", "bin")
    bad = c.with_columns(step=pl.col("xfg").diff().over("method")).filter(
        (pl.col("step") <= 0) | ~pl.col("fg").is_between(0, 1)).select("method", "bin", "xfg", "fg")
    return Check("calibration_bins", "calibration bins: predicted FG% rises bin to bin; observed within 0-1", c.height, bad)


def xpts_out_of_time(metrics) -> Check:
    """Last season's xPTS model, frozen, on this season's shots: calibration slope 0.85-1.15,
    intercept within 0.1, better than a constant FG%."""
    m = metrics["xpts"]
    cmp = m.get("compare", {}).get("prior_frozen")
    if cmp is None:
        return Check("xpts_out_of_time", "no prior season: nothing to test out of time", 0, pl.DataFrame(), blocking=False)
    row = pl.DataFrame([{**cmp, "log_loss_constant": m["log_loss_constant"]}])
    bad = row.filter(~pl.col("calibration_slope").is_between(0.85, 1.15) | (pl.col("calibration_intercept").abs() > 0.1)
                     | (pl.col("log_loss") >= pl.col("log_loss_constant")))
    return Check("xpts_out_of_time", f"last season's model on this season: slope {cmp['calibration_slope']}, "
                 f"intercept {cmp['calibration_intercept']}, log loss {cmp['log_loss']}", 1, bad, blocking=False)


def stat_reliability(a) -> Check:
    """Every rate has positive sampling noise and true spread (a finite stabilization point)."""
    r = a["stat_reliability"]
    if r.height and r["players"].max() < 30:
        return Check("stat_reliability", "too few players to fit stabilization points", r.height, pl.DataFrame(),
                     blocking=False)
    bad = r.filter(~((pl.col("sigma") > 0) & (pl.col("tau") > 0) & pl.col("k").is_finite() & (pl.col("k") > 0)))
    return Check("stat_reliability", f"finite stabilization point k for every rate (fitted on {r['fitted_on'][0]})",
                 r.height, bad.select("stat", "sigma", "tau", "k"))


def split_half(a) -> Check:
    """Model check: the odd/even-game correlation the fitted noise and spread imply = the observed
    one, within two standard errors of a correlation over that many players (at least 0.1)."""
    r = a["stat_reliability"]
    tol = pl.max_horizontal(pl.lit(0.1), 2 * (1 - pl.col("split_r_model") ** 2) / (pl.col("split_players") - 1).sqrt())
    bad = r.filter((pl.col("split_r") - pl.col("split_r_model")).abs() > tol)
    return Check("split_half", "observed odd/even-game correlation = implied by sigma, tau (2 SE)", r.height,
                 bad.select("stat", "split_players", "split_r", "split_r_model"), blocking=False)


def prior_backtest(a) -> Check:
    """Predicting the rest of the season from its first 4 games: padding beats the raw rate,
    last season helps (or does no harm), and 90% intervals cover 80-97% of what happened."""
    b = a["prior_backtest"]
    if b.height == 0:
        return Check("prior_backtest", "no backtest (no career data)", 0, pl.DataFrame(), blocking=False)
    b4 = b.filter(pl.col("games") == 4)
    bad = b4.filter((pl.col("rmse_padded") >= pl.col("rmse_raw")) | (pl.col("rmse_prior") > pl.col("rmse_padded"))
                    | ~pl.col("coverage_90").is_between(0.8, 0.97))
    return Check("prior_backtest", f"first 4 games -> rest of season: padded < raw, with last season <= padded, "
                 f"90% intervals cover 80-97% (fitted on {b['fitted_on'][0]})", b4.height,
                 bad.select("stat", "rmse_raw", "rmse_padded", "rmse_prior", "coverage_90"), blocking=False)


def player_stable(a) -> Check:
    """A padded rate lies between its sources (this season, last season, league); the weights sum to 1."""
    t = a["player_stable"]
    if t.height == 0:
        return Check("player_stable", "no padded rates (too few games for any stabilization point)", 0, pl.DataFrame(),
                     blocking=False)
    lo = pl.min_horizontal(pl.col("raw").fill_null(pl.col("league_rate")), pl.col("prior").fill_null(pl.col("league_rate")), "league_rate")
    hi = pl.max_horizontal(pl.col("raw").fill_null(pl.col("league_rate")), pl.col("prior").fill_null(pl.col("league_rate")), "league_rate")
    bad = t.filter(~pl.col("estimate").is_between(lo - 1e-9, hi + 1e-9)
                   | ((pl.col("w_season") + pl.col("w_prior") + pl.col("w_league") - 1).abs() > 1e-9)
                   | ~((pl.col("lo") <= pl.col("estimate")) & (pl.col("estimate") <= pl.col("hi"))))
    return Check("player_stable", "padded rates between this season, last season and league; weights sum to 1",
                 t.height, bad.select("player_id", "stat", "raw", "prior", "league_rate", "estimate"))
