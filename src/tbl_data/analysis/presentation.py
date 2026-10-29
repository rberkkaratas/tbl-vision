"""Presentation fields for the Tableau files: Turkish number formatting, short names and
small long-format tables for KPI strips and cards.

Tableau's STR() always prints a decimal point and text tables of several measures need
Measure Names/Values, so text that a viewer reads (KPI values, cards, labels) is formatted
here once, in Turkish (106,9 · %55,1 · +12,9 · 8.357), and Tableau only places it.
"""

from __future__ import annotations

import math

import polars as pl

from .matchup import AUTO, IDENTITY
from .stability import ON_OFF, STATS, UNIT


def num(v, d: int = 1, sign: bool = False) -> str | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = f"{abs(v):,.{d}f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    prefix = "-" if v < 0 and round(abs(v), d) != 0 else ("+" if sign and v > 0 and round(v, d) != 0 else "")
    return prefix + s


def pct(v, d: int = 1) -> str | None:
    return None if v is None else "%" + num(v * 100, d)


def _fmt(col: str, f) -> pl.Expr:
    return pl.col(col).map_elements(f, return_dtype=pl.String, skip_nulls=False)


def short_name(name: str) -> str:
    """"Tayfun Erülkü (Yalovaspor)" -> "T. Erülkü": short names appear inside one team's views."""
    parts = name.split(" (")[0].split()
    return name if len(parts) < 2 else f"{parts[0][0]}. {parts[-1]}"


# short names people use for the clubs; a club not listed gets its first word, title-cased
NICKNAMES = {"BALIKESİR BÜYÜKŞEHİR BELEDİYE SPOR": "Balıkesir", "BÜYÜKÇEKMECE BASKETBOL": "Büyükçekmece",
             "CEDİ OSMAN BASKETBOL": "Cedi Osman", "DARÜŞŞAFAKA": "Darüşşafaka", "FENERBAHÇE GELİŞİM": "Fenerbahçe G.",
             "FİNALSPOR": "Finalspor", "GAZİANTEP BASKETBOL": "Gaziantep", "GÖZTEPE ISONEM": "Göztepe",
             "iLab BASKETBOL": "iLab", "KEÇİÖREN BELEDİYESİ BAĞLUM": "Keçiören", "KİPAŞ K.B.B.": "Kipaş",
             "KONYA BÜYÜKŞEHİR BELEDİYESİ SPOR": "Konya BB", "MERSİN SPOR": "Mersin", "MKE ANKARAGÜCÜ": "Ankaragücü",
             "OGM ORMANSPOR": "Ormanspor", "TED ANKARA KOLEJLİLER": "TED Kolejliler", "YALOVA HAREM SPOR": "Harem"}


def _title(word: str) -> str:
    return word[:1] + word[1:].replace("I", "ı").replace("İ", "i").lower()


def team_codes(teams: pl.DataFrame) -> dict[str, str]:
    """team name -> short display name."""
    return {r["name"]: NICKNAMES.get(r["name"]) or _title(r["name"].split()[0]) for r in teams.iter_rows(named=True)}


def augment(files: dict[str, pl.DataFrame], codes: dict[str, str]) -> dict[str, pl.DataFrame]:
    f = dict(files)
    code = lambda c: pl.col(c).replace_strict(codes, default=pl.col(c))  # noqa: E731
    for k in ("team_profiles", "four_factors", "possession_starts", "team_shooting", "clutch_team", "game_flow",
              "player_profiles", "player_shooting", "pairs", "assist_pairs", "runs", "shots", "player_percentiles"):
        if "team" in f[k].columns:
            f[k] = f[k].with_columns(team_short=pl.when(pl.col("team") == "Lig ortalaması").then(pl.lit("LİG"))
                                     .otherwise(code("team")))
    f["game_flow"] = f["game_flow"].with_columns(opponent_short=code("opponent"))
    f["shots"] = f["shots"].with_columns(opponent_short=code("opponent"))

    # a player who changed clubs mid-season has a row per club; the Oyuncu parameter filters by
    # name, so those names carry the club: "Tayfun Erülkü (Yalovaspor)"
    per_club = f["player_profiles"].group_by("player").agg(pl.col("team").n_unique())
    moved = per_club.filter(pl.col("team") > 1)["player"]
    for k, cols in {"player_profiles": ["player"], "player_percentiles": ["player"], "player_shooting": ["player"],
                    "player_stable": ["player"],
                    "player_zone_shooting": ["player"], "clutch_player": ["player"], "shots": ["player", "assist_player"],
                    "pairs": ["player_a", "player_b"], "assist_pairs": ["passer", "scorer"]}.items():
        f[k] = f[k].with_columns(pl.when(pl.col(c).is_in(moved)).then(pl.format("{} ({})", pl.col(c), code("team")))
                                 .otherwise(pl.col(c)).alias(c) for c in cols)

    # team KPI strip (team rows + the league row)
    tp = f["team_profiles"]
    kpis = [("ORtg", "ortg", lambda v: num(v)), ("DRtg", "drtg", lambda v: num(v)), ("Net", "net", lambda v: num(v, 1, True)),
            ("Tempo", "pace", lambda v: num(v)), ("eFG%", "efg", pct), ("TS%", "ts", pct),
            ("xPTS/şut", "xpts_per_shot", lambda v: num(v, 2)), ("Hücum", "poss", lambda v: num(v, 0))]
    rows = []
    n_teams = tp.filter(~pl.col("is_league")).height
    for r in tp.iter_rows(named=True):
        header = "Lig ortalaması" if r["is_league"] else \
            f"{codes.get(r['team'], r['team'])} · {r['wins']}G–{r['losses']}M"
        for i, (label, col, fmt) in enumerate(kpis, 1):
            v = r[col]
            if r["is_league"] and col == "poss":  # the league row sums every team: show one team's share
                v = v / n_teams
            rows.append({"team": r["team"], "is_league": r["is_league"], "header": header, "metric": label,
                         "metric_order": i, "value_text": fmt(v)})
    f["team_kpis"] = pl.DataFrame(rows)

    # four factors: z-score of "better than league" within each factor, short names, order
    ff = f["four_factors"]
    short = {"eFG%": ("eFG%", 1), "Top kaybı %": ("Kayıp %", 2), "Hücum ribaundu %": ("Ribaund %", 3),
             "Savunma ribaundu %": ("Ribaund %", 3), "Serbest atış oranı": ("FT oranı", 4)}
    f["four_factors"] = ff.with_columns(
        z=(pl.col("better") / pl.col("better").std().over("factor", "side")).round(2),
        factor_short=pl.col("factor").replace_strict({k: v[0] for k, v in short.items()}),
        factor_order=pl.col("factor").replace_strict({k: v[1] for k, v in short.items()}, return_dtype=pl.Int32),
        value_text=_fmt("value", pct), league_text=_fmt("league", pct),
        net=pl.col("team").replace_strict(dict(tp.select("team", "net").iter_rows()), default=None))

    # possession starts: league order and readable labels
    ps = f["possession_starts"]
    order = ps.group_by("start").agg(pl.col("league_share").first()).sort("league_share", descending=True)
    f["possession_starts"] = ps.join(order.with_row_index("start_order", 1).drop("league_share"), on="start").with_columns(
        ppp_text=_fmt("ppp", lambda v: num(v, 2)), share_text=_fmt("share", lambda v: pct(v, 0)))

    # players
    pp = f["player_profiles"].with_columns(short=_fmt("player", short_name))
    f["player_profiles"] = pp.with_columns(on_off_text=_fmt("on_off", lambda v: num(v, 1, True)))
    card = [("Dk/maç", "mpg", lambda v: num(v)), ("Sayı/maç", "ppg", lambda v: num(v)), ("Rib./maç", "rpg", lambda v: num(v)),
            ("Asist/maç", "apg", lambda v: num(v)), ("TS%", "ts", pct), ("USG%", "usg", pct)]
    rows = []
    for r in pp.iter_rows(named=True):
        sub = " · ".join(x for x in [r["nationality"], f"{int(r['age'])} yaş" if r["age"] else None,
                                     f"{r['games']} maç", f"{int(r['minutes'])} dk"] if x)
        if not r["qualified"]:
            sub += f" · {r['qualify_min']} dk altı: yüzdelik yok"
        for i, (label, col, fmt) in enumerate(card, 1):
            rows.append({"player": r["player"], "team": r["team_short"], "sub": sub, "metric": label, "metric_order": i,
                         "value_text": fmt(r[col])})
    f["player_card"] = pl.DataFrame(rows)

    groups = {"ppg": ("Hacim", 1), "pts40": ("Hacim", 2), "usg": ("Hacim", 3), "ts": ("Şut", 4), "efg": ("Şut", 5),
              "xpts_per_shot": ("Şut", 6), "poe_per_shot": ("Şut", 7), "fg3a_rate": ("Şut", 8), "ft_rate": ("Şut", 9),
              "ast_pct": ("Oyun kurma", 10), "tov_pct": ("Oyun kurma", 11), "orb_pct": ("Ribaund", 12),
              "drb_pct": ("Ribaund", 13), "stl_pct": ("Savunma", 14), "blk_pct": ("Savunma", 15), "on_off": ("Etki", 16)}
    fmts = {"ppg": lambda v: num(v), "pts40": lambda v: num(v), "on_off": lambda v: num(v, 1, True),
            "xpts_per_shot": lambda v: num(v, 2), "poe_per_shot": lambda v: num(v, 2, True)}
    pc = f["player_percentiles"]
    f["player_percentiles"] = pc.with_columns(
        group=pl.col("metric").replace_strict({k: v[0] for k, v in groups.items()}),
        metric_order=pl.col("metric").replace_strict({k: v[1] for k, v in groups.items()}, return_dtype=pl.Int32),
        value_text=pl.struct("metric", "value").map_elements(
            lambda s: fmts.get(s["metric"], pct)(s["value"]), return_dtype=pl.String),
        pct_class=pl.when(pl.col("metric").is_in(["usg", "fg3a_rate"])).then(pl.lit("Stil (iyi/kötü değil)"))
        .when(pl.col("pct") >= 80).then(pl.lit("Üst %20")).when(pl.col("pct") >= 60).then(pl.lit("60–80"))
        .when(pl.col("pct") >= 40).then(pl.lit("40–60")).when(pl.col("pct") >= 20).then(pl.lit("20–40"))
        .otherwise(pl.lit("Alt %20")))

    ps_ = f["player_shooting"]
    f["player_shooting"] = ps_.with_columns(
        short=_fmt("player", short_name),
        noise=pl.when(pl.col("poe_per_shot").abs() >= 2 * pl.col("poe_se")).then(pl.lit("Fark şans payından büyük"))
        .otherwise(pl.lit("Şans payı içinde")),
        interval_text=pl.struct("poe_per_shot", "poe_se").map_elements(
            lambda s: f"{num(s['poe_per_shot'] - 2 * s['poe_se'], 2, True)} … {num(s['poe_per_shot'] + 2 * s['poe_se'], 2, True)}",
            return_dtype=pl.String))

    pr = f["pairs"]
    f["pairs"] = pr.with_columns(
        pair=pl.struct("player_a", "player_b").map_elements(
            lambda s: f"{short_name(s['player_a'])} + {short_name(s['player_b'])}", return_dtype=pl.String),
        apart_net=(100 * ((pl.col("a_without_b_pts_for") + pl.col("b_without_a_pts_for"))
                          / (pl.col("a_without_b_poss_for") + pl.col("b_without_a_poss_for"))
                          - (pl.col("a_without_b_pts_against") + pl.col("b_without_a_pts_against"))
                          / (pl.col("a_without_b_poss_against") + pl.col("b_without_a_poss_against")))).round(1),
        net_se=pl.when((pl.col("poss_for") > 0) & (pl.col("poss_against") > 0))
        .then((110 * (1 / pl.col("poss_for") + 1 / pl.col("poss_against")).sqrt()).round(0)),
    ).with_columns(sample=pl.format("{} hücum · ±{}", pl.col("poss"), pl.col("net_se").cast(pl.Int32).fill_null(0)),
                   gain=(pl.col("net") - pl.col("apart_net")).round(1))
    pr = f["pairs"]
    f["pairs_long"] = pl.concat([
        pr.select("team", "team_short", "player_a", "player_b", "pair", "poss", "minutes", "net", "sample",
                  value=pl.col("net"), kind=pl.lit("Birlikte")),
        pr.select("team", "team_short", "player_a", "player_b", "pair", "poss", "minutes", "net", "sample",
                  value=pl.col("apart_net"), kind=pl.lit("Biri kenardayken")),
    ])
    f["assist_pairs"] = f["assist_pairs"].with_columns(passer_short=_fmt("passer", short_name),
                                                       scorer_short=_fmt("scorer", short_name))

    # games
    gf = f["game_flow"]
    f["game_flow"] = gf.with_columns(
        result=pl.when(pl.col("won")).then(pl.lit("Kazandı")).otherwise(pl.lit("Kaybetti")),
        lead_min=(pl.col("time_leading_s") / 60).round(1),
        comeback=pl.format("{} {}–{} {} · {}. hf", pl.col("team_short"), pl.col("pts"), pl.col("opp_pts"),
                           pl.col("opponent_short"), pl.col("week")),
        # 1 = the biggest deficit a winner overcame (a whole season has too many comebacks to list)
        comeback_rank=pl.when(pl.col("won")).then(
            pl.col("largest_deficit").rank("ordinal", descending=True).over("won")))
    summary = [("Sayı", "pts", 0), ("En büyük fark", "biggest_lead", 0), ("En uzun seri", "biggest_run", 0),
               ("Önde (dk)", "lead_min", 1), ("Eşitlik", "times_tied", 0), ("Liderlik değişimi", "lead_changes_game", 0),
               ("En çok geride", "largest_deficit", 0)]
    rows = []
    for r in f["game_flow"].iter_rows(named=True):
        for i, (label, col, d) in enumerate(summary, 1):
            rows.append({"match_id": r["match_id"], "team": r["team_short"], "is_home": r["is_home"], "result": r["result"],
                         "metric": label, "metric_order": i, "value_text": num(r[col], d)})
    f["match_summary"] = pl.DataFrame(rows)

    def clock(period, s_in_period, length):
        left = length - s_in_period
        lab = f"{period}. çeyrek" if period <= 4 else f"Uzatma {period - 4}"
        return f"{lab} {int(left // 60)}:{int(left % 60):02d}"

    def period_start(p):
        return (p - 1) * 600 if p <= 4 else 2400 + (p - 5) * 300

    st = f["score_timeline"]
    f["score_timeline"] = st.with_columns(
        clock_label=pl.struct("period_ord", "elapsed_s").map_elements(
            lambda s: clock(s["period_ord"], s["elapsed_s"] - period_start(s["period_ord"]),
                            600 if s["period_ord"] <= 4 else 300), return_dtype=pl.String),
        score_label=pl.format("{}–{}", pl.col("home_score"), pl.col("away_score")),
        home_short=code("home"), away_short=code("away"),
        match=pl.format("{}. hf · {} {}–{} {}", pl.col("week"), pl.col("home_code").fill_null(pl.col("home")),
                        pl.col("home_score").max().over("match_id"), pl.col("away_score").max().over("match_id"),
                        pl.col("away_code").fill_null(pl.col("away"))))
    f["runs"] = f["runs"].with_columns(
        run_time=pl.struct("start_period", "start_s").map_elements(
            lambda s: clock(s["start_period"], s["start_s"] - period_start(s["start_period"]),
                            600 if s["start_period"] <= 4 else 300), return_dtype=pl.String),
        run_text=pl.format("{}–0", pl.col("points")),
        margin_change=pl.struct("margin_before", "margin_after").map_elements(
            lambda s: f"{num(s['margin_before'], 0, True) or '0'} → {num(s['margin_after'], 0, True) or '0'}",
            return_dtype=pl.String))
    f["clutch_team"] = f["clutch_team"].with_columns(
        label=pl.struct("net", "poss").map_elements(lambda s: f"{num(s['net'], 1, True)} ({s['poss']} hücum)",
                                                     return_dtype=pl.String))
    f.update(reliability_views(f["stat_reliability"], f["xpts_calibration"], f["prior_backtest"], f["player_stable"]))
    return f


# how each rate reads: format, order on the player table
RATE_FMT = {"xpts_per_shot": lambda v: num(v, 2), "poe_per_shot": lambda v: num(v, 2, True),
            ON_OFF: lambda v: num(v, 1, True)}
RATE_ORDER = ["ts", "efg", "fg2_pct", "fg3_pct", "ft_pct", "xpts_per_shot", "poe_per_shot", "fg3a_rate", "ft_rate",
              "usg", "ast_pct", "tov_pct", "orb_pct", "drb_pct", "stl_pct", "blk_pct", ON_OFF]
LABEL = {**{s.name: s.label for s in STATS}, ON_OFF: "On/off net"}
STABLE_COLS = ["Bu sezon", "Deneme", "Geçen sezon", "Dengeli", "%90 aralık"]
CAL_METHODS = {"in_season": "Yalnız bu sezon (5 katlı)", "prior_frozen": "Geçen sezonun modeli (dondurulmuş)",
               "pooled": "Geçen + bu sezon"}


def _rate(stat: str, v) -> str | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else RATE_FMT.get(stat, pct)(v)


def reliability_views(rel: pl.DataFrame, cal: pl.DataFrame, backtest: pl.DataFrame,
                      stable: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Long, pre-formatted tables for the Güven dashboard."""
    shown = [x for x in RATE_ORDER if x in set(rel["stat"])]
    order = {x: i for i, x in enumerate(RATE_ORDER, 1)}
    finite = lambda c: pl.when(pl.col(c).is_finite()).then(pl.col(c))  # noqa: E731
    r = rel.filter(pl.col("stat").is_in(shown)).with_columns(
        label=pl.col("stat").replace_strict(LABEL), metric_order=pl.col("stat").replace_strict(order, return_dtype=pl.Int32),
        k=finite("k"), k_lo=finite("k_lo"), k_hi=finite("k_hi"), k_games=finite("k_games"))
    r = r.with_columns(
        k_games_lo=pl.col("k_lo") / pl.col("units_per_game"), k_games_hi=pl.col("k_hi") / pl.col("units_per_game"),
        settles=pl.when(pl.col("k_games") <= pl.col("season_games") / 4).then(pl.lit("Çeyrek sezonda"))
        .when(pl.col("k_games") <= pl.col("season_games")).then(pl.lit("Bir sezon içinde"))
        .otherwise(pl.lit("Bir sezon yetmez")))
    r = r.with_columns(
        k_text=pl.struct("k", "k_games", "unit").map_elements(
            lambda x: None if x["k"] is None else f"{num(x['k_games'], 1)} maç · {num(x['k'], 0)} {x['unit']}",
            return_dtype=pl.String),
        # an upper bound beyond a few seasons only says "very slow"
        interval_text=pl.struct("k_games_lo", "k_games_hi", "season_games").map_elements(
            lambda x: f"≥ {num(x['k_games_lo'], 1)} maç" if x["k_games_hi"] is None
            or x["k_games_hi"] > 3 * x["season_games"] else f"{num(x['k_games_lo'], 1)} – {num(x['k_games_hi'], 1)} maç",
            return_dtype=pl.String),
        season_text=_fmt("season_reliability", lambda v: pct(v, 0)))

    # observed minus predicted FG% per bin of predicted FG%, in points: a calibrated model hugs 0
    c = cal.with_columns(method_label=pl.when(pl.col("used") & (pl.col("method") == "in_season"))
                         .then(pl.lit("Kullanılan: yalnız bu sezon (5 katlı)"))
                         .otherwise(pl.col("method").replace_strict(CAL_METHODS)),
                         gap=((pl.col("fg") - pl.col("xfg")) * 100).round(2))

    if backtest.height:
        b = backtest.filter((pl.col("games") == 4) & pl.col("stat").is_in(shown))
        b = pl.concat([b.select("stat", "with_prior", method=pl.lit(m), method_order=pl.lit(i),
                                value=(100 * pl.col(col) / pl.col("rmse_raw_with_prior")).round(1))
                       for i, (m, col) in enumerate([("Ham değer", "rmse_raw_with_prior"),
                                                     ("Lig ortalamasıyla", "rmse_padded_with_prior"),
                                                     ("+ geçen sezon", "rmse_prior_with_prior")], 1)])
        b = b.with_columns(label=pl.col("stat").replace_strict(LABEL),
                           metric_order=pl.col("stat").replace_strict(order, return_dtype=pl.Int32),
                           value_text=_fmt("value", lambda v: num(v, 0)))
    else:
        b = pl.DataFrame(schema={"stat": pl.String, "with_prior": pl.Int64, "method": pl.String, "method_order": pl.Int32,
                                 "value": pl.Float64, "label": pl.String, "metric_order": pl.Int32, "value_text": pl.String})

    rows = []
    for x in stable.filter(pl.col("stat").is_in(shown)).iter_rows(named=True):
        st = x["stat"]
        prev = "–" if x["prior"] is None else (
            f"{_rate(st, x['prior'])}" + ("" if x["prior_source"] == "TBL" else " *"))
        vals = [_rate(st, x["raw"]) or "–", num(x["units"], 0), prev, _rate(st, x["estimate"]),
                f"{_rate(st, x['lo'])}–{_rate(st, x['hi']).lstrip('%')}"]
        tip_units = f"{num(x['units'], 0)} {UNIT[st]}" + (
            f" · geçen sezon {num(x['prior_units'], 0)} ({x['prior_source']})" if x["prior"] is not None else "")
        weights = (f"bu sezon {pct(x['w_season'], 0)} · geçen sezon {pct(x['w_prior'], 0)} · "
                   f"lig ortalaması {pct(x['w_league'], 0)}")
        for i, (col, v) in enumerate(zip(STABLE_COLS, vals), 1):
            rows.append({"player": x["player"], "team": x["team"], "stat": st, "label": LABEL[st],
                         "metric_order": order[st], "col": col, "col_order": i, "value_text": v,
                         "units_text": tip_units, "weights_text": weights})
    ps = pl.DataFrame(rows) if rows else pl.DataFrame(schema={
        "player": pl.String, "team": pl.String, "stat": pl.String, "label": pl.String, "metric_order": pl.Int64,
        "col": pl.String, "col_order": pl.Int64, "value_text": pl.String, "units_text": pl.String,
        "weights_text": pl.String})
    return {"stat_reliability": r, "xpts_calibration": c, "prior_backtest": b, "player_stable": ps}


# -- Rakip Analizi -------------------------------------------------------------------------
TIER_MARK = {"oturdu": "●", "kısmen": "◐", "gürültü": "○"}
TIER_RANK = {"oturdu": 2, "kısmen": 1, "gürültü": 0}
FACTOR_LABEL = {"efg": "eFG%", "tov": "Top kaybı %", "orb": "Hücum ribaundu %", "ftr": "FT oranı (FTM/FGA)",
                "r3": "3PA oranı (stil)"}
# the reliability stat of the attacker's value and of the defender's allowed value
FACTOR_REL = {"efg": ("efg", "d_efg"), "tov": ("tov", "d_tov"), "orb": ("orb", "drb"), "ftr": ("ftr", "d_ftr"),
              "r3": ("r3", "d_r3")}
ZONE_LABEL = {"rim": ("Pota dibi", 1), "paint": ("Boya", 2), "midrange": ("Orta mesafe", 3),
              "corner_3": ("Köşe üçlük", 4), "above_break_3": ("Yay üstü üçlük", 5)}
PERIOD_ORDER = {"Ç1": 1, "Ç2": 2, "Ç3": 3, "Ç4": 4, "Uzatma": 5}
VENUE_TEXT = {"Ev": "evinde", "Deplasman": "deplasmanda", "Tarafsız": "tarafsız sahada"}
SCOUT_PLAYERS = 10  # players per team on the scout table and the rotation
IDENTITY_ORDER = {key: i for i, (key, *_) in enumerate(IDENTITY, 1)}


def _lowest(*tiers: str | None) -> str | None:
    t = [x for x in tiers if x]
    return min(t, key=TIER_RANK.get) if t else None


def matchup_views(t: dict[str, pl.DataFrame], team_name: dict[int, str], codes: dict[str, str],
                  names: pl.DataFrame, metrics: dict | None, display: dict[tuple[int, int], str] | None = None
                  ) -> dict[str, pl.DataFrame]:
    """Long, pre-formatted tables for the Rakip Analizi dashboard; teams by name (the parameters' values).
    display: (team, player id) -> the name the Oyuncu parameter uses (a mid-season transfer carries the club)."""
    display = display or {}
    short = lambda tid: codes.get(team_name.get(tid), team_name.get(tid))  # noqa: E731
    rel = {r["stat"]: r["tier"] for r in t["team_reliability"].iter_rows(named=True)}
    out: dict[str, pl.DataFrame] = {}

    # projection strip: one row per pair x court x column
    rows = []
    for r in t["matchup_pairs"].iter_rows(named=True):
        a, b = short(r["team_season_id"]), short(r["opp_id"])
        court = VENUE_TEXT[r["court"]]
        title = f"{a} – {b} · {a} {court}"
        if r["fixture_week"] is not None:
            fx = (f"Sıradaki maç: {r['fixture_week']}. hafta, {r['fixture_start']:%d.%m.%Y} · {a} {court}"
                  if r["venue"] == AUTO else f"Fikstürde {r['fixture_week']}. haftada karşılaşıyorlar")
        else:
            fx = "Fikstürde yaklaşan maç yok" + (" · ev sahibi varsayıldı" if r["venue"] == AUTO else "")
        cols = [("Tempo", num(r["pace"])), (f"{a} · 100 hücumda", num(r["ortg"])), (f"{b} · 100 hücumda", num(r["opp_ortg"])),
                ("Beklenen skor", f"{round(r['pts'])}–{round(r['opp_pts'])}"),
                ("Fark (%80 aralık)", f"{num(r['margin'], 1, True)}  ({num(r['lo80'], 0, True)} … {num(r['hi80'], 0, True)})"),
                (f"{a} kazanır", pct(r["p_win"], 0))]
        for i, (col, v) in enumerate(cols, 1):
            rows.append({"team": team_name[r["team_season_id"]], "opponent": team_name[r["opp_id"]], "venue": r["venue"],
                         "col": ["Tempo", "Ana takım ORtg", "Rakip ORtg", "Beklenen skor", "Fark", "Kazanma"][i - 1],
                         "col_label": col, "col_order": i, "value_text": v, "title_text": title, "fixture_text": fx,
                         "court": r["court"],
                         "fixture_ts": int(r["fixture_start"].timestamp()) if r["fixture_start"] is not None else None})
    out["mx_projection"] = pl.DataFrame(rows)

    # four factors: two blocks per pair
    rows = []
    for r in t["matchup_factors"].iter_rows(named=True):
        a, b, k = r["team_season_id"], r["opp_id"], r["factor"]
        att, dfn = (a, b) if r["direction"] == "offense" else (b, a)
        block = f"{short(att)} hücumu × {short(dfn)} savunması"
        fmt = (lambda v: num(v, 3)) if k == "ftr" else (lambda v: pct(v))
        tr = _lowest(rel.get(FACTOR_REL[k][0]), rel.get(FACTOR_REL[k][1]))
        imp = r["impact"]
        tone = "Nötr" if imp is None or abs(imp) < 1 else ("Ana takım yönünde" if imp > 0 else "Rakip yönünde")
        vals = [f"{fmt(r['att_value'])} · {r['att_rank']}.", f"{fmt(r['def_value'])} · {r['def_rank']}.",
                fmt(r["league_value"]), fmt(r["expected"]),
                "–" if imp is None else num(imp, 1, True), f"{TIER_MARK.get(tr, '')} {tr or ''}".strip()]
        for i, (col, v) in enumerate(zip(["Hücum eden", "Savunmanın izni", "Lig", "Beklenen", "Etki (100 hücum)", "Güven"],
                                         vals), 1):
            rows.append({"team": team_name[a], "opponent": team_name[b], "block": block, "factor": k,
                         "block_order": 1 if r["direction"] == "offense" else 2, "factor_label": FACTOR_LABEL[k],
                         "factor_order": list(FACTOR_LABEL).index(k) + 1, "col": col, "col_order": i, "value_text": v,
                         "tone": tone if col == "Etki (100 hücum)" else "Nötr"})
    out["mx_factors"] = pl.DataFrame(rows)

    # identity cards: team x metric
    fmts = {"adj_net": lambda v: num(v, 1, True), "adj_o": num, "adj_d": num, "pace": num, "q1net": lambda v: num(v, 1, True),
            "xps": lambda v: num(v, 2), "o_xps": lambda v: num(v, 2), "ftr": lambda v: num(v, 3)}
    rows = []
    idt = t["team_identity"]
    for r in idt.iter_rows(named=True):
        v = fmts.get(r["metric"], pct)(r["value"])
        rows.append({"team": team_name[r["team_season_id"]], "team_short": short(r["team_season_id"]),
                     "metric_key": r["metric"], "tier": r["tier"],
                     "metric": f"{r['label']} {TIER_MARK.get(r['tier'], '')}".strip(),
                     "metric_order": IDENTITY_ORDER[r["metric"]],
                     "value_text": v + (f" · {r['rank']}." if r["rank"] is not None else "")})
    for r in idt.unique("team_season_id").iter_rows(named=True):
        rows.append({"team": team_name[r["team_season_id"]], "team_short": short(r["team_season_id"]),
                     "metric_key": "clutch", "tier": "gürültü", "metric": "Clutch net ○", "metric_order": 99,
                     "value_text": "–" if r["clutch_net"] is None else f"{num(r['clutch_net'], 1, True)} ({r['clutch_poss']} hücum)"})
    out["mx_identity"] = pl.DataFrame(rows)

    # shot diet by zone
    z = t["matchup_zones"].with_columns(
        team=c_name("team_season_id", team_name).fill_null("Lig ortalaması"),
        team_short=pl.col("team_season_id").replace_strict({k: short(k) for k in team_name}, default="LİG",
                                                           return_dtype=pl.String),
        zone_label=pl.col("zone").replace_strict({k: v[0] for k, v in ZONE_LABEL.items()}),
        zone_order=pl.col("zone").replace_strict({k: v[1] for k, v in ZONE_LABEL.items()}, return_dtype=pl.Int32),
        share_text=_fmt("share", lambda v: pct(v, 0)), fg_text=_fmt("fg", pct), xpts_text=_fmt("xpts_per_shot", lambda v: num(v, 2)))
    out["mx_zones"] = z

    # quarters: scored, allowed, margin per game
    pp_ = t["period_profile"]
    rows = []
    for r in pp_.iter_rows(named=True):
        for i, (m, v, sign) in enumerate([("Attığı", r["pts_pg"], False), ("Yediği", r["opp_pts_pg"], False),
                                          ("Fark", r["pts_pg"] - r["opp_pts_pg"], True)], 1):
            rows.append({"team": team_name[r["team_season_id"]], "team_short": short(r["team_season_id"]),
                         "period": r["period"], "period_order": PERIOD_ORDER[r["period"]], "metric": m, "metric_order": i,
                         "value": round(v, 2), "value_text": num(v, 1, sign)})
    out["mx_periods"] = pl.DataFrame(rows)

    # opponent (or own) players: who carries the offence and how well
    prof = t["player_profiles"].with_columns(rank=pl.col("minutes").rank("ordinal", descending=True).over("team_season_id")
                                              ).filter(pl.col("rank") <= SCOUT_PLAYERS)
    st = t["player_stable"].filter(pl.col("stat").is_in(["ts", "fg3_pct"]))
    stab = {(r["team_season_id"], r["player_id"], r["stat"]): r for r in st.iter_rows(named=True)}
    zs = t["player_zone_shooting"]
    rim = dict(((r["team_season_id"], r["player_id"]), r["shots"]) for r in zs.filter(pl.col("zone") == "rim").iter_rows(named=True))
    tot = {(a, b): n for a, b, n in zs.group_by("team_season_id", "player_id").agg(pl.col("shots").sum()).iter_rows()}
    rows = []
    for r in prof.iter_rows(named=True):
        key = (r["team_season_id"], r["player_id"])

        def est(stat):
            s = stab.get((*key, stat))
            if s is None or s["estimate"] is None:
                return "–"
            return f"{pct(s['estimate'])} ±{num(50 * (s['hi'] - s['lo']), 0)}"  # half the 90% interval, in points
        n_shots = tot.get(key, 0)
        vals = [num(r["mpg"]), f"{r['games']} ({r['starts']})", pct(r["usg"]), est("ts"), pct(r["fg3a_rate"], 0),
                num(r["ft_rate"], 2), pct(r["ast_pct"]), pct(r["tov_pct"]), pct(r["orb_pct"]),
                pct(rim.get(key, 0) / n_shots, 0) if n_shots else "–", est("fg3_pct")]
        label = f"{short_name(r['player'])}"
        for i, (col, v) in enumerate(zip(["Dk/maç", "Maç (ilk 5)", "USG%", "TS% dengeli", "3PA oranı", "FT oranı",
                                           "AST%", "TOV%", "ORB%", "Pota dibi payı", "3P% dengeli"], vals), 1):
            rows.append({"team": r["team"], "player": display.get(key, r["player"]), "player_label": label,
                         "player_order": r["rank"],
                         "col": col, "col_order": i, "value_text": v or "–"})
    out["mx_players"] = pl.DataFrame(rows)

    # rotation: minutes per team game by quarter and in the last 5 minutes of Q4
    rot = t["rotation"].join(names, on="player_id", how="left").with_columns(
        total=pl.col("seconds") / 60 / pl.col("team_games"))
    rank = rot.unique(["team_season_id", "player_id"]).with_columns(
        order=pl.col("total").rank("ordinal", descending=True).over("team_season_id")).select("team_season_id", "player_id", "order")
    rot = rot.join(rank, on=["team_season_id", "player_id"]).filter((pl.col("order") <= SCOUT_PLAYERS) & (pl.col("period") != "Uzatma"))
    order = {"Ç1": 1, "Ç2": 2, "Ç3": 3, "Ç4": 4, "Son 5 dk": 5}
    out["mx_rotation"] = rot.with_columns(
        team=c_name("team_season_id", team_name), player_label=_fmt("player", short_name),
        col=pl.col("period").replace_strict({"Son 5 dk": "Ç4 son 5 dk"}, default=pl.col("period")),
        col_order=pl.col("period").replace_strict(order, return_dtype=pl.Int32), mpg=pl.col("mpg").round(2),
        value_text=_fmt("mpg", lambda v: num(v)),
        starter_text=pl.format("{} maç, {} ilk 5", pl.col("games").fill_null(0), pl.col("starts").fill_null(0)),
        player=pl.struct("team_season_id", "player_id", "player").map_elements(
            lambda x: display.get((x["team_season_id"], x["player_id"]), x["player"]), return_dtype=pl.String),
    ).select("team", "player", "player_label", "order", "col", "col_order", "mpg", "value_text", "starter_text")

    # most used fives
    nm = dict(names.iter_rows())
    rows = []
    for r in t["top_lineups"].iter_rows(named=True):
        surnames = sorted(nm.get(p, str(p)).split(" (")[0].split()[-1] for p in r["lineup"])
        label = "·".join(_tr_upper(x[:3]) for x in surnames)  # a row header fits ~25 characters
        poss = r["poss_for"] + r["poss_against"]
        se = 110 * (1 / r["poss_for"] + 1 / r["poss_against"]) ** 0.5 if r["poss_for"] and r["poss_against"] else None
        vals = [num(r["seconds"] / 60, 0), str(r["games"]), str(poss),
                "–" if r["net"] is None else f"{num(r['net'], 1, True)} ±{round(se)}"]
        for i, (col, v) in enumerate(zip(["Dakika", "Maç", "Hücum", "Net (±1 SE)"], vals), 1):
            rows.append({"team": team_name[r["team_season_id"]], "lineup_label": label, "lineup_names": " · ".join(surnames),
                         "order": r["rank"], "col": col, "col_order": i, "value_text": v})
    out["mx_lineups"] = pl.DataFrame(rows)

    # form: margin minus expected, last games
    f = t["team_form"].with_columns(
        team=c_name("team_season_id", team_name),
        opponent_short=pl.col("opp_id").replace_strict({k: short(k) for k in team_name}, default=None),
        team_short=pl.col("team_season_id").replace_strict({k: short(k) for k in team_name}, default=None),
        tone=pl.when(pl.col("residual") >= 0).then(pl.lit("Beklentinin üstü")).otherwise(pl.lit("Beklentinin altı")))
    out["mx_form"] = f.with_columns(
        game_label=pl.format("{}. hf · {} ({})", pl.col("week"), pl.col("opponent_short"),
                             pl.when(pl.col("is_home")).then(pl.lit("e")).otherwise(pl.lit("d"))),
        score_text=pl.format("{}–{}", pl.col("pts"), pl.col("opp_pts")),
        expected_text=_fmt("expected", lambda v: num(v, 1, True)), residual_text=_fmt("residual", lambda v: num(v, 1, True))).drop("start_utc")

    # head-to-head, both seasons; a pair without meetings gets one row saying so
    h = t["head_to_head"]
    rows = []
    for r in h.sort("start_utc").iter_rows(named=True):
        res = "Hükmen" if r["forfeit"] else ("Kazandı" if r["pts"] > r["opp_pts"] else "Kaybetti")
        label = f"{r['season_key']} · {r['week']}. hf · {'evinde' if r['is_home'] else 'deplasmanda'}"
        rows.append({"team": team_name[r["team_season_id"]], "opponent": team_name[r["opp_id"]], "row": label,
                     "row_order": int(r["start_utc"].timestamp()), "score_text": f"{r['pts']}–{r['opp_pts']}", "result": res})
    met = {(x["team"], x["opponent"]) for x in rows}
    for a in team_name.values():
        for b in team_name.values():
            if a != b and (a, b) not in met:
                rows.append({"team": a, "opponent": b, "row": "Son iki sezonda karşılaşma yok", "row_order": 0,
                             "score_text": "", "result": ""})
    out["mx_h2h"] = pl.DataFrame(rows)

    # standings: wins first, then the adjusted net rating (the strength the projection uses)
    tr = t["team_ratings"].sort(["wins", "adj_net"], descending=True).with_row_index("rank", 1)
    adj_rank = dict(tr.select("team_season_id", pl.col("adj_net").rank("min", descending=True)).iter_rows())
    rows = []
    for r in tr.iter_rows(named=True):
        vals = [short(r["team_season_id"]), f"{r['wins']}–{r['games'] - r['wins']}", num(r["net"], 1, True),
                num(r["adj_net"], 1, True), f"{int(adj_rank[r['team_season_id']])}."]
        for i, (col, v) in enumerate(zip(["Takım", "G–M", "Net", "Düz. net", "Güç"], vals), 1):
            rows.append({"team": team_name[r["team_season_id"]], "team_short": short(r["team_season_id"]),
                         "row": f"{r['rank']}. {short(r['team_season_id'])}",
                         "rank": r["rank"], "col": col, "col_order": i, "value_text": v})
    out["home_table"] = pl.DataFrame(rows)

    out.update(scout_views(t, team_name, short, rel, display))

    m = (metrics or {}).get("matchup", {})
    bt, pb = m.get("backtest", {}), m.get("prior_backtest", {})

    def bt_text(s: dict, season: str | None) -> str:
        if not s.get("games"):
            return ""
        return (f"{season}: {s['games']} maç, kazanan %{num(100 * s['accuracy'], 0)} doğru, ortalama hata "
                f"{num(s['mae'], 1)} sayı, Brier {num(s['brier'], 3)} (yalnız ev avantajı {num(s['brier_home'], 3)})")
    out["mx_meta"] = pl.DataFrame([{
        "backtest_text": bt_text(bt, "Bu sezon"), "prior_text": bt_text(pb, m.get("prior_season")),
        "sigma": m.get("sigma"), "sigma_from": m.get("sigma_from"), "home_pts": m.get("home_pts"),
        "weights_text": " · ".join(f"{FACTOR_LABEL[k].split(' (')[0]} {num(v, 2, True)}" for k, v in m.get("weights", {}).items()),
        "reliability_from": m.get("reliability_from"), "games_now": m.get("games_now")}])
    return out


def _tr_upper(s: str) -> str:
    return s.replace("i", "İ").replace("ı", "I").upper()


def c_name(col: str, team_name: dict[int, str]) -> pl.Expr:
    return pl.col(col).replace_strict(team_name, default=None, return_dtype=pl.String)


# -- Rakip Analizi: the coach's read (keys, style, personnel) ------------------------------------
# A key's rank is its effect times how far the numbers can be trusted after this many games: in a walk-forward
# test on 2025-26 the matchup expectation predicted a single game's four factors only ~1% better than the
# team's own average, so these are season tendencies, not a forecast of one game.
TIER_WEIGHT = {"oturdu": 1.0, "kısmen": 0.6, "gürültü": 0.25}
# Coach-facing text describes what the numbers show and leaves the call to the coach: no verdicts
# ("lehimize", "durdurulacak"), ranks and reliability spelled out.
SIGNAL = {"oturdu": "güçlü ●", "kısmen": "orta ◐", "gürültü": "zayıf ○"}
TOPIC = {"efg": "Şut verimi (eFG%)", "tov": "Top kaybı", "orb": "Hücum ribaundu", "ftr": "Serbest atış (FTM/şut)"}
WHAT = {"efg": "eFG", "tov": "top kaybı oranı", "orb": "hücum ribaundu oranı", "ftr": "serbest atış/şut"}
LETS = {"efg": "izin veriyor", "tov": "yaptırıyor", "orb": "izin veriyor", "ftr": "izin veriyor"}
N_KEYS = 3  # matchup keys; the fourth card is the player to stop
# style profile: (identity metric, label, reliability stat, descriptor when high, when low); right = more / better
STYLE = [("pace", "Tempo", "pace", "hızlı oynar", "yavaş oynar"), ("r3", "Üçlük deneme oranı", "r3", "çok üçlük atar", "az üçlük atar"),
         ("trans", "Geçiş hücumu payı", "trans", "çok koşar", "az koşar"), ("rim", "Pota dibi şut payı", "rim", "potaya çok girer", "potaya az girer"),
         ("mid", "Orta mesafe payı", "mid", "orta mesafeyi sever", "orta mesafeden kaçar"),
         ("ftr", "Serbest atış oranı", "ftr", "çok faul alır", "az faul alır"),
         ("xps", "Aldığı şut kalitesi", "xps", "ligin üst üçte birinde", "ligin alt üçte birinde"),
         ("adj_o", "Hücum reytingi (düz.)", "ortg", "ligin üst üçte birinde", "ligin alt üçte birinde"),
         ("adj_d", "Savunma reytingi (düz.)", "drtg", "ligin üst üçte birinde", "ligin alt üçte birinde"),
         ("o_xps", "Rakibe verdiği şut kalitesi", "o_xps", "ligin üst üçte birinde", "ligin alt üçte birinde")]
LOWER_BETTER = {"adj_d", "o_xps"}  # flipped so that right is better
ROLE_PCT = 0.8  # a role tag: in the top fifth of the league's qualified players
CARD_PLAYERS = 5


def _toward(v: float | None, a: str, b: str) -> str:
    """Which way a season tendency points, without calling it good or bad."""
    if v is None or abs(v) < 0.5:
        return "Belirgin bir yön yok"
    return f"Eğilim: {num(v, 1, True)} sayı/100 hücum, {a if v > 0 else b} yönü"


def scout_views(t: dict[str, pl.DataFrame], team_name: dict[int, str], short, rel: dict[str, str | None],
                display: dict[tuple[int, int], str]) -> dict[str, pl.DataFrame]:
    """mx_keys (keys to the game per pair), mx_style (team style percentiles), mx_roles (player cards)."""
    out: dict[str, pl.DataFrame] = {}
    teams = list(team_name)

    # players: role tags against the league's qualified players
    prof = t["player_profiles"]
    zs = t["player_zone_shooting"]
    shots = {(a, b): n for a, b, n in zs.group_by("team_season_id", "player_id").agg(pl.col("shots").sum()).iter_rows()}
    rim = {(a, b): n for a, b, n in zs.filter(pl.col("zone") == "rim").select("team_season_id", "player_id", "shots").iter_rows()}
    prof = prof.with_columns(rim_share=pl.struct("team_season_id", "player_id").map_elements(
        lambda x: (rim.get((x["team_season_id"], x["player_id"]), 0) / shots[(x["team_season_id"], x["player_id"])])
        if shots.get((x["team_season_id"], x["player_id"])) else None, return_dtype=pl.Float64))
    q = prof.filter(pl.col("qualified"))
    cut = {c: q[c].drop_nulls().quantile(ROLE_PCT) if q[c].drop_nulls().len() else None
           for c in ("usg", "ast_pct", "fg3a_rate", "rim_share", "ft_rate", "orb_pct", "drb_pct")}
    stab = {(r["team_season_id"], r["player_id"], r["stat"]): r for r in
            t["player_stable"].filter(pl.col("stat").is_in(["ts", "fg3_pct"])).iter_rows(named=True)}

    def roles(r) -> str:
        tags = [lab for col, lab in (("usg", "Topu çok kullanır"), ("ast_pct", "Çok asist yapar"),
                                     ("fg3a_rate", "Çok üçlük dener"), ("rim_share", "Potaya çok girer"),
                                     ("ft_rate", "Çok faul alır"))
                if r[col] is not None and cut[col] is not None and r[col] >= cut[col]]
        if any(r[c] is not None and cut[c] is not None and r[c] >= cut[c] for c in ("orb_pct", "drb_pct")):
            tags.append("Çok ribaund alır")
        return " · ".join(tags[:2]) or "Belirgin bir uç değeri yok"  # two tags fit a card line

    def est(r, stat):
        x = stab.get((r["team_season_id"], r["player_id"], stat))
        return "–" if x is None or x["estimate"] is None else f"{pct(x['estimate'], 0)} ±{num(50 * (x['hi'] - x['lo']), 0)}"

    cards, key_player = [], {}
    for tid in teams:
        top = prof.filter(pl.col("team_season_id") == tid).sort("minutes", descending=True).head(CARD_PLAYERS)
        for i, r in enumerate(top.iter_rows(named=True), 1):
            name = short_name(r["player"])
            lines = [roles(r), f"{num(r['mpg'])} dk · {num(r['ppg'])} sayı · USG {pct(r['usg'], 0)}",
                     f"TS {est(r, 'ts')} · 3P {est(r, 'fg3_pct')}",
                     f"Pota {pct(r['rim_share'], 0) if r['rim_share'] is not None else '–'} · "
                     f"3PA {pct(r['fg3a_rate'], 0)} · AST {pct(r['ast_pct'], 0)}"]
            cards.append({"team": team_name[tid], "player": display.get((tid, r["player_id"]), r["player"]),
                          "card": f"{i}. {name}", "card_order": i, **{f"l{j}": v for j, v in enumerate(lines, 1)}})
        if top.height:  # the player to stop: who carries most of the offence among the regulars
            k = top.sort("usg", descending=True).row(0, named=True)
            key_player[tid] = (k, roles(k))
    out["mx_roles"] = pl.DataFrame(cards)

    # stand-out matchups: per pair, the four factors ranked by effect x reliability, then the busiest player
    fac = t["matchup_factors"].filter(pl.col("factor").is_in(["efg", "tov", "orb", "ftr"]))
    n_teams = len(teams)
    # "en yüksek k.": each team's place from the top, on what it does in attack and what it allows in defence
    off = fac.filter(pl.col("direction") == "offense").unique(["team_season_id", "factor"])
    top_att = {(r["team_season_id"], r["factor"]): int(r["k"]) for r in off.with_columns(
        k=pl.col("att_value").rank("min", descending=True).over("factor")).iter_rows(named=True)}
    dfn = fac.filter(pl.col("direction") == "offense").unique(["opp_id", "factor"])
    top_def = {(r["opp_id"], r["factor"]): int(r["k"]) for r in dfn.with_columns(
        k=pl.col("def_value").rank("min", descending=True).over("factor")).iter_rows(named=True)}
    rows = []
    for (a, b), g in fac.group_by("team_season_id", "opp_id"):
        sa, sb = short(a), short(b)
        cand = []
        for r in g.iter_rows(named=True):
            k = r["factor"]
            tier = _lowest(rel.get(FACTOR_REL[k][0]), rel.get(FACTOR_REL[k][1]))
            imp = r["impact"]
            att, dfd = (a, b) if r["direction"] == "offense" else (b, a)
            fmt = (lambda v: num(v, 2)) if k == "ftr" else (lambda v: pct(v))
            cand.append((abs(imp or 0) * TIER_WEIGHT.get(tier, 0.25), {
                "key": f"{TOPIC[k]} · {short(att)} hücumda",
                # short lines: a card is ~40 characters wide; the ranks are explained once in the section line
                "l1": f"{short(att)}: {fmt(r['att_value'])} · ligde {top_att[(att, k)]}./{n_teams}",
                "l2": f"{short(dfd)} savunması: {fmt(r['def_value'])} · {top_def[(dfd, k)]}./{n_teams}",
                "l3": f"Lig ort. {fmt(r['league_value'])} · birleşim {fmt(r['expected'])}",
                "l4": _toward(imp, sa, sb),
                "l5": f"Veri gücü: {SIGNAL.get(tier, SIGNAL['gürültü'])}"}))
        cand.sort(key=lambda x: -x[0])
        for i, (score, c) in enumerate(cand[:N_KEYS], 1):
            rows.append({"team": team_name[a], "opponent": team_name[b], "key_order": i, "score": round(score, 2), **c})
        if b in key_player:
            k, tags = key_player[b]
            rows.append({"team": team_name[a], "opponent": team_name[b], "key_order": N_KEYS + 1, "score": None,
                         "key": f"Topu en çok kullanan · {short_name(k['player'])}",
                         "l1": f"USG {pct(k['usg'], 0)} · {num(k['ppg'])} sayı · {num(k['mpg'])} dk",
                         "l2": f"Asist oranı {pct(k['ast_pct'], 0)} · TS {est(k, 'ts')}",
                         "l3": tags,
                         "l4": "",
                         "l5": "Veri gücü: kullanım ● · isabet ◐"})
    out["mx_keys"] = pl.DataFrame(rows)

    # style: each team's league percentile on the settled style metrics (right = more, or better)
    idt = t["team_identity"]
    rows = []
    for order, (key, label, stat, hi, lo) in enumerate(STYLE, 1):
        m = idt.filter(pl.col("metric") == key).select("team_season_id", "value")
        if m.is_empty():
            continue
        sign = -1 if key in LOWER_BETTER else 1
        m = m.with_columns(pct=((sign * pl.col("value")).rank("average") - 1) / max(m.height - 1, 1) * 100)
        tier = rel.get(stat)
        fmt = {"pace": num, "adj_o": num, "adj_d": num, "xps": lambda v: num(v, 2), "o_xps": lambda v: num(v, 2),
               "ftr": lambda v: num(v, 3)}.get(key, pct)
        for r in m.iter_rows(named=True):
            desc = hi if r["pct"] >= 67 else lo if r["pct"] <= 33 else "lig ortasında"
            rows.append({"team": team_name[r["team_season_id"]], "team_short": short(r["team_season_id"]),
                         "metric": f"{label} {TIER_MARK.get(tier, '')}".strip(), "metric_key": key,
                         "metric_order": order, "pct": round(r["pct"], 1), "value_text": fmt(r["value"]), "desc": desc})
    out["mx_style"] = pl.DataFrame(rows)
    return out
