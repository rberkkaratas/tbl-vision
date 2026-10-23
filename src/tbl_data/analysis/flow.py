"""Game flow: the score after every basket, leads, ties, scoring runs and clutch time.

The definitions follow FIBA LiveStats, whose own per-game numbers (match_teams
biggest_lead, biggest_run, lead_changes, times_tied, time_leading_s) are the check:
  - a lead change is a team going ahead after the other one led (a tie in between does
    not reset it); a tie is the score becoming level after 0-0, and a period that starts
    level counts as another one;
  - a run is the points one team scores in a row while the other scores none;
  - clutch (NBA definition): the last 5 minutes of the 4th quarter or overtime with the
    margin at 5 or less when the possession starts.
"""

from __future__ import annotations

import polars as pl

POINTS = {"2pt": 2, "3pt": 3, "freethrow": 1}
RUN_MIN = 8  # the runs table keeps runs of at least this many points
CLUTCH_S, CLUTCH_MARGIN = 300, 5

TIMELINE_SCHEMA = {"match_id": pl.Int32, "seq": pl.Int32, "action_number": pl.Int32, "period_ord": pl.Int32,
                   "elapsed_s": pl.Float64, "team_season_id": pl.Int32, "player_id": pl.Int32,
                   "home_scored": pl.Boolean, "points": pl.Int32, "home_score": pl.Int32, "away_score": pl.Int32}


def period_end_s(period_ord: int) -> float:
    return 600.0 * period_ord if period_ord <= 4 else 2400.0 + 300.0 * (period_ord - 4)


def score_timeline(events: pl.DataFrame, match_teams: pl.DataFrame) -> pl.DataFrame:
    """One row per scoring action (canonical order), plus 0-0 at tip-off and the final score."""
    home_of = dict(match_teams.filter(pl.col("is_home")).select("match_id", "team_season_id").iter_rows())
    rows = []
    for (match_id,), g in sorted(events.sort("seq").partition_by("match_id", as_dict=True).items()):
        h = a = 0
        rows.append({"match_id": match_id, "seq": 0, "period_ord": 1, "elapsed_s": 0.0, "points": 0,
                     "home_score": 0, "away_score": 0})
        for e in g.iter_rows(named=True):
            if e["action_type"] in POINTS and e["success"] and e["team_season_id"] is not None:
                pts, home = POINTS[e["action_type"]], e["team_season_id"] == home_of[match_id]
                h, a = (h + pts, a) if home else (h, a + pts)
                rows.append({"match_id": match_id, "seq": e["seq"], "action_number": e["action_number"],
                             "period_ord": e["period_ord"], "elapsed_s": e["elapsed_s"],
                             "team_season_id": e["team_season_id"], "player_id": e["player_id"],
                             "home_scored": home, "points": pts, "home_score": h, "away_score": a})
        last = g.row(-1, named=True)
        rows.append({"match_id": match_id, "seq": last["seq"] + 1, "period_ord": last["period_ord"],
                     "elapsed_s": period_end_s(last["period_ord"]), "points": 0, "home_score": h, "away_score": a})
    return pl.DataFrame([{k: r.get(k) for k in TIMELINE_SCHEMA} for r in rows], schema=TIMELINE_SCHEMA).with_columns(
        margin_home=pl.col("home_score") - pl.col("away_score"))


def game_flow(timeline: pl.DataFrame, match_teams: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """timeline -> (one row per team-game, runs of RUN_MIN+ points)."""
    sides = {(m, h): t for m, t, h in match_teams.select("match_id", "team_season_id", "is_home").iter_rows()}
    flow, runs = [], []
    for (match_id,), g in sorted(timeline.sort("seq").partition_by("match_id", as_dict=True).items()):
        rs = list(g.iter_rows(named=True))
        side = {True: {"biggest_lead": 0, "lead_changes": 0, "time_leading_s": 0.0, "biggest_run": 0},
                False: {"biggest_lead": 0, "lead_changes": 0, "time_leading_s": 0.0, "biggest_run": 0}}
        ties, leader, last_leader, prev_t, period = 0, None, None, 0.0, 1
        run: list[dict] = []  # consecutive scoring rows of one side
        for r in rs:
            if leader is not None:  # the previous score stood until now
                side[leader]["time_leading_s"] += r["elapsed_s"] - prev_t
            prev_t = r["elapsed_s"]
            if not r["points"]:
                continue
            if r["period_ord"] != period:
                if leader is None and last_leader is not None:  # level at the break
                    ties += 1
                period = r["period_ord"]
            m = r["margin_home"]
            leader = None if m == 0 else m > 0
            if leader is None:
                ties += 1
            else:
                if last_leader is not None and leader != last_leader:
                    side[leader]["lead_changes"] += 1
                last_leader = leader
                side[leader]["biggest_lead"] = max(side[leader]["biggest_lead"], abs(m))
            if run and run[0]["home_scored"] != r["home_scored"]:
                runs += _run(match_id, run, sides)
                run = []
            run.append(r)
            s = side[r["home_scored"]]
            s["biggest_run"] = max(s["biggest_run"], sum(x["points"] for x in run))
        runs += _run(match_id, run, sides)
        final = rs[-1]
        for home in (True, False):
            own, opp = (final["home_score"], final["away_score"]) if home else (final["away_score"], final["home_score"])
            sign = 1 if home else -1
            flow.append({"match_id": match_id, "team_season_id": sides[(match_id, home)],
                         "opponent_id": sides[(match_id, not home)], "is_home": home, "pts": own, "opp_pts": opp,
                         "won": own > opp, **side[home], "times_tied": ties,
                         "largest_deficit": max(0, *(-sign * r["margin_home"] for r in rs))})
    f = pl.DataFrame(flow).with_columns(pl.col("time_leading_s").round(1),
                                         lead_changes_game=pl.col("lead_changes").sum().over("match_id"))
    r = pl.DataFrame(runs, schema={"match_id": pl.Int32, "team_season_id": pl.Int32, "points": pl.Int32,
                                   "start_period": pl.Int32, "start_s": pl.Float64, "end_s": pl.Float64,
                                   "duration_s": pl.Float64, "margin_before": pl.Int32, "margin_after": pl.Int32})
    return f, r


def _run(match_id: int, run: list[dict], sides: dict) -> list[dict]:
    pts = sum(r["points"] for r in run)
    if pts < RUN_MIN:
        return []
    first, last, home = run[0], run[-1], run[0]["home_scored"]
    sign = 1 if home else -1
    return [{"match_id": match_id, "team_season_id": sides[(match_id, home)], "points": pts,
             "start_period": first["period_ord"], "start_s": first["elapsed_s"], "end_s": last["elapsed_s"],
             "duration_s": round(last["elapsed_s"] - first["elapsed_s"], 1),
             "margin_before": sign * (first["margin_home"] - sign * first["points"]),  # the runner's margin
             "margin_after": sign * last["margin_home"]}]


def clutch(possessions: pl.DataFrame, timeline: pl.DataFrame, events: pl.DataFrame,
           match_teams: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """-> (clutch possessions, team totals, player totals)."""
    tl = timeline.select("match_id", "seq", "margin_home").sort("seq")
    home = match_teams.select("match_id", "team_season_id", "is_home")
    p = possessions.sort("start_seq").join_asof(
        tl.rename({"seq": "start_seq"}), on="start_seq", by="match_id", strategy="backward",
        allow_exact_matches=False, check_sortedness=False)  # score before the possession's first event; sorted above
    p = p.join(home, on=["match_id", "team_season_id"]).with_columns(
        remaining_s=pl.when(pl.col("period_ord") <= 4).then(600.0 * pl.col("period_ord"))
        .otherwise(2400.0 + 300.0 * (pl.col("period_ord") - 4)) - pl.col("start_s"),
        margin_offense=pl.when(pl.col("is_home")).then(pl.col("margin_home")).otherwise(-pl.col("margin_home")))
    cp = p.filter((pl.col("period_ord") >= 4) & (pl.col("remaining_s") <= CLUTCH_S)
                  & (pl.col("margin_offense").abs() <= CLUTCH_MARGIN))
    keys = cp.select("match_id", "possession_no")
    # points per team in clutch possessions, including technical FTs by the defence
    pts = events.join(keys, on=["match_id", "possession_no"]).filter(
        pl.col("action_type").is_in(list(POINTS)) & pl.col("success") & pl.col("team_season_id").is_not_null()
    ).with_columns(pts=pl.col("action_type").replace_strict(POINTS, return_dtype=pl.Int32))
    scored = pts.group_by("match_id", "team_season_id").agg(pts=pl.col("pts").sum())
    n = cp.group_by("match_id", "team_season_id").agg(poss=pl.len())
    tg = home.join(n, on=["match_id", "team_season_id"], how="left").join(
        scored, on=["match_id", "team_season_id"], how="left").fill_null(0)
    tg = tg.join(tg.select("match_id", opp_poss="poss", opp_pts="pts", opp="team_season_id"), on="match_id").filter(
        pl.col("opp") != pl.col("team_season_id"))
    team = tg.group_by("team_season_id").agg(
        clutch_games=(pl.col("poss") + pl.col("opp_poss") > 0).sum(), poss=pl.col("poss").sum(),
        pts=pl.col("pts").sum(), opp_poss=pl.col("opp_poss").sum(), opp_pts=pl.col("opp_pts").sum()).with_columns(
        ortg=pl.when(pl.col("poss") > 0).then((100 * pl.col("pts") / pl.col("poss")).round(1)),
        drtg=pl.when(pl.col("opp_poss") > 0).then((100 * pl.col("opp_pts") / pl.col("opp_poss")).round(1))
    ).with_columns(net=(pl.col("ortg") - pl.col("drtg")).round(1))
    ev = events.join(keys, on=["match_id", "possession_no"]).filter(pl.col("player_id").is_not_null())
    a, ok = pl.col("action_type"), pl.col("success")
    player = ev.group_by("team_season_id", "player_id").agg(
        games=pl.col("match_id").n_unique(),
        pts=(a.replace_strict(POINTS, default=0, return_dtype=pl.Int32) * ok.cast(pl.Int32)).sum(),
        fga=a.is_in(["2pt", "3pt"]).sum(), fgm=(a.is_in(["2pt", "3pt"]) & ok).sum(),
        fg3a=(a == "3pt").sum(), fg3m=((a == "3pt") & ok).sum(),
        fta=(a == "freethrow").sum(), ftm=((a == "freethrow") & ok).sum(),
        tov=(a == "turnover").sum(), ast=(a == "assist").sum())
    return cp.drop("is_home"), team, player
