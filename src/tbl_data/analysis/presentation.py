"""Presentation fields for the Tableau files: Turkish number formatting, short names and
small long-format tables for KPI strips and cards.

Tableau's STR() always prints a decimal point and text tables of several measures need
Measure Names/Values, so text that a viewer reads (KPI values, cards, labels) is formatted
here once, in Turkish (106,9 · %55,1 · +12,9 · 8.357), and Tableau only places it.
"""

from __future__ import annotations

import math

import polars as pl


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
    parts = name.split()
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

    # team KPI strip (team rows + the league row)
    tp = f["team_profiles"]
    kpis = [("ORtg", "ortg", lambda v: num(v)), ("DRtg", "drtg", lambda v: num(v)), ("Net", "net", lambda v: num(v, 1, True)),
            ("Tempo", "pace", lambda v: num(v)), ("eFG%", "efg", pct), ("TS%", "ts", pct),
            ("xPTS/şut", "xpts_per_shot", lambda v: num(v, 2)), ("Hücum", "poss", lambda v: num(v, 0))]
    rows = []
    for r in tp.iter_rows(named=True):
        header = "Lig ortalaması" if r["is_league"] else \
            f"{codes.get(r['team'], r['team'])} · {r['wins']}G–{r['losses']}M"
        for i, (label, col, fmt) in enumerate(kpis, 1):
            rows.append({"team": r["team"], "is_league": r["is_league"], "header": header, "metric": label,
                         "metric_order": i, "value_text": fmt(r[col])})
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
                           pl.col("opponent_short"), pl.col("week")))
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
    return f
