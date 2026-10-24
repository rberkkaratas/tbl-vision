"""What the Tableau workbook shows: parameters, data sources with their calculated fields,
worksheets, dashboards and click actions (written to XML by workbook.py).

Design: grey for everything, orange for the selection, blue/red only for better/worse than
the league. Parameters (Takım, Oyuncu, Maç, ...) connect every data source: each source has
calculated fields that read them, and clicking a team, a player or a game sets them
(clicking the selected one again clears it). Text a viewer reads is pre-formatted in Turkish
(presentation.py); the workbook locale is tr_TR so axis numbers use decimal commas.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from .workbook import Axis, Calc, Dashboard, F, Param, ParamAction, RefLine, Sheet, Source, Workbook, Zone

INK, SEC, RULE = "#1b2430", "#5b6673", "#e3e6ea"
ACCENT, ACCENT_LIGHT, GREY, LOW = "#e8590c", "#f6b48a", "#b4bcc5", "#e1e5ea"
GOOD, GOOD_L, MID, BAD_L, BAD = "#2166ac", "#92c5de", "#dcdcdc", "#f4a582", "#b2182b"
ALL = "Tümü"
NAV = ["Takımlar", "Şut Kalitesi", "Oyuncular", "İkililer", "Maçlar", "Yöntem"]

T_TITLE = {"fontname": "Tableau Semibold", "fontsize": "20", "fontcolor": INK}
T_SUB = {"fontname": "Tableau Book", "fontsize": "10", "fontcolor": SEC}
T_NOTE_B = {"fontname": "Tableau Semibold", "fontsize": "9", "fontcolor": SEC}
T_NOTE = {"fontname": "Tableau Book", "fontsize": "9", "fontcolor": SEC}
T_FOOT = {"fontname": "Tableau Book", "fontsize": "8", "fontcolor": SEC}
T_H = {"fontname": "Tableau Semibold", "fontsize": "12", "fontcolor": INK}
T_BODY = {"fontname": "Tableau Book", "fontsize": "10", "fontcolor": INK}
T_SHEET = {"fontname": "Tableau Semibold", "fontsize": "12", "fontcolor": INK}
T_SHEET2 = {"fontname": "Tableau Book", "fontsize": "9", "fontcolor": SEC}
TIP_B = {"bold": "true", "fontsize": "10", "fontcolor": INK}
TIP = {"fontsize": "9", "fontcolor": SEC}
TIP_V = {"bold": "true", "fontsize": "9", "fontcolor": INK}
NL = ("Æ\n", {})  # a line break inside Tableau formatted text

FMT_0 = "n#,##0;-#,##0"
FMT_1 = "n#,##0.0;-#,##0.0"
FMT_2 = "n#,##0.00;-#,##0.00"
FMT_S2 = "*+#,##0.00;-#,##0.00;0.00"
FMT_P0 = "p0%"
FMT_P1 = "p0.0%"

TEAM_COLORS = {ALL: SEC, "Seçili takım": ACCENT, "Diğer": GREY}
PLAYER_COLORS = {"Seçili oyuncu": ACCENT, "Seçili takım": ACCENT_LIGHT, ALL: SEC, "Diğer": GREY}


def title(main: str, second: str | None = None, dynamic: list[tuple[str, dict]] | None = None) -> list[tuple[str, dict]]:
    runs = [(main, T_SHEET)] + (dynamic or [])
    if second:
        runs += [NL, (second, T_SHEET2)]
    return runs


def tip(*parts) -> list[tuple[str, dict]]:
    """tip("<[none:team:nk]>", NL, "ORtg ", "<[avg:ortg:qk]>"): fields bold, text grey, the first field bigger."""
    runs, first = [], True
    for p in parts:
        if p == NL:
            runs.append(NL)
            first = False
        elif p.startswith("<"):
            runs.append((p, TIP_B if first else TIP_V))
        else:
            runs.append((p, TIP))
    return runs


def team_block(short: bool = True) -> list[Calc]:
    out = [
        Calc("takim_filtre", "Takım Filtresi", f'{{Takım}} = "{ALL}" OR [team] = {{Takım}}', "boolean"),
        Calc("takim_vurgu", "Takım Vurgu", f'IF {{Takım}} = "{ALL}" THEN "{ALL}" ELSEIF [team] = {{Takım}} '
                                           f'THEN "Seçili takım" ELSE "Diğer" END', "string"),
        Calc("takim_tik", "Takım Tık", f'IF [team] = {{Takım}} THEN "{ALL}" ELSE [team] END', "string"),
        Calc("sabit_tumu", "Sabit Tümü", f'"{ALL}"', "string"),
    ]
    if short:
        out.append(Calc("takim_etiket", "Takım Etiketi",
                        'IF [team] = {Takım} THEN "▶ " + [team_short] ELSE [team_short] END', "string"))
    return out


def player_block(top_player_case: str) -> list[Calc]:
    return [
        Calc("oyuncu_tik", "Oyuncu Tık", f'IF [player] = {{Oyuncu}} THEN "{ALL}" ELSE [player] END', "string"),
        Calc("oyuncu_vurgu", "Oyuncu Vurgu",
             f'IF [player] = {{Oyuncu}} THEN "Seçili oyuncu" ELSEIF {{Takım}} <> "{ALL}" AND [team] = {{Takım}} '
             f'THEN "Seçili takım" ELSEIF {{Oyuncu}} = "{ALL}" AND {{Takım}} = "{ALL}" THEN "{ALL}" ELSE "Diğer" END',
             "string"),
        Calc("kisa_ad", "Kısa Ad", 'LEFT([player], 1) + ". " + SPLIT([player], " ", -1)', "string"),
        Calc("oyuncu_etiket", "Oyuncu Etiketi",
             f'IF [player] = {{Oyuncu}} OR ({{Takım}} <> "{ALL}" AND [team] = {{Takım}}) '
             'THEN [Calculation_kisa_ad] END', "string"),
        Calc("profil_oyuncu", "Profil Oyuncusu",
             f'IF {{Oyuncu}} <> "{ALL}" THEN {{Oyuncu}} ELSE {top_player_case} END', "string"),
    ]


def frame(heading: str, subtitle: str, note: str | None, footer: str) -> list[Zone]:
    zones = [Zone("blank", 0, 0, 1200, 4, background=ACCENT),
             Zone("text", 16, 10, 560, 32, runs=[(heading, T_TITLE)]),
             Zone("text", 16, 42, 900, 20, runs=[(subtitle, T_SUB)]),
             Zone("blank", 16, 66, 1168, 1, background=RULE),
             Zone("text", 16, 778, 1168, 18, runs=[(footer, T_FOOT)])]
    if note:
        zones.append(Zone("text", 16, 116, 1168, 30, runs=[("Nasıl okunur · ", T_NOTE_B), (note, T_NOTE)]))
    for i, target in enumerate(NAV):
        zones.append(Zone("nav", 1184 - (len(NAV) - i) * 104, 16, 100, 28, target=target, title=target))
    return zones


def design(directory: Path) -> Workbook:
    d = directory
    tp = pl.read_csv(d / "team_profiles.csv")
    league = tp.filter(pl.col("is_league")).row(0, named=True)
    nick = dict(tp.filter(~pl.col("is_league")).select("team", "team_short").iter_rows())
    teams = sorted(nick, key=lambda t: nick[t].replace("i", "ı").lower())
    gf = pl.read_csv(d / "game_flow.csv", try_parse_dates=True)
    n_games, weeks = gf["match_id"].n_unique(), (gf["week"].min(), gf["week"].max())
    pp = pl.read_csv(d / "player_profiles.csv")
    psh = pl.read_csv(d / "player_shooting.csv").filter(pl.col("shots") >= 20)
    q = pp.filter(pl.col("qualified"))

    def rng(v: pl.Series, step: float, default: tuple[float, float]) -> tuple[float, float]:
        import math
        v = v.drop_nulls()
        if v.is_empty():
            return default
        return round(math.floor(v.min() / step) * step, 3), round(math.ceil(v.max() / step) * step, 3)

    x_xpts, y_ts = rng(psh["xpts_per_shot"], 0.05, (0.8, 1.4)), rng(q["ts"], 0.05, (0.3, 0.9))
    short_case = "CASE [Calculation_gosterilen_takim] " + " ".join(
        f'WHEN "{t}" THEN "{n}"' for t, n in nick.items()) + " END"
    season = d.name.split("-", 1)[1] if "-" in d.name else d.name  # tableau/tbl-2025-26 -> 2025-26
    n_teams = len(nick)
    playoffs = (gf["phase"] != "regular").any()
    span = "normal sezon ve play-off" if playoffs else "normal sezon"
    per_team = gf.group_by("team_season_id").len()["len"]
    g_lo, g_hi = int(per_team.min()), int(per_team.max())
    games_txt = f"{g_lo}–{g_hi}" if g_lo != g_hi else str(g_lo)
    rating_se = round(110 / (tp.filter(~pl.col("is_league"))["poss"].mean() ** 0.5))  # 1 SE of a team rating
    qmin = int(pp["qualify_min"][0])
    footer = (f"Veri: TBF ve FIBA LiveStats kamuya açık maç verisi · {n_games} maç, {weeks[0]}–{weeks[1]}. hafta · "
              f"TBL {season} {span} · tbl-vision")

    # showcase defaults, so that no view is empty when nothing is selected
    showcase_team = tp.filter(~pl.col("is_league")).sort("net", descending=True)["team"][0]
    ranked = pp.filter(pl.col("qualified")) if pp["qualified"].any() else pp  # one-game test data has no 100-minute player
    top = ranked.sort("minutes", descending=True).group_by("team", maintain_order=True).first()
    top_of = dict(top.select("team", "player").iter_rows())
    top_case = ("CASE {Takım} " + " ".join(f'WHEN "{t}" THEN "{p}"' for t, p in top_of.items())
                + f' ELSE "{top_of[showcase_team]}" END')
    shown_team = (f'IF {{Oyuncu}} <> "{ALL}" AND NOT ISNULL([Calculation_oyuncunun_takimi]) '
                  f'THEN [Calculation_oyuncunun_takimi] ELSEIF {{Takım}} <> "{ALL}" THEN {{Takım}} '
                  f'ELSE "{showcase_team}" END')
    comeback = gf.filter(pl.col("won")).sort("largest_deficit", descending=True).row(0, named=True)
    matches = gf.filter(pl.col("is_home")).sort("date", "match_id")
    match_members = [(int(r["match_id"]), f"{r['week']}. hf · {r['team_short']} {r['pts']}–{r['opp_pts']} {r['opponent_short']}")
                     for r in matches.iter_rows(named=True)]

    p_team = Param("Takım", "string", ALL, members=[(ALL, None)] + [(t, nick[t]) for t in teams])
    players = pp.sort("player")
    p_player = Param("Oyuncu", "string", ALL, members=[(ALL, None)] + [
        (r["player"], f"{r['player']} ({r['team_short']})") for r in players.iter_rows(named=True)])
    p_side = Param("Taraf", "string", "Hücum", members=[("Hücum", None), ("Savunma", None)])
    p_ctx = Param("Şut Bağlamı", "string", ALL, members=[(v, None) for v in
                                                        (ALL, "Hızlı hücum", "Set hücum", "İkinci şans",
                                                         "Top kaybı sonrası", "Clutch")])
    p_map = Param("Harita Rengi", "string", "Şut değeri (xPTS)",
                  members=[("Şut değeri (xPTS)", None), ("Şut başarısı (POE)", None)])
    p_min_shots = Param("Min. şut", "integer", 50, range=(20, 100, 10))
    p_min_poss = Param("Min. hücum (ikili)", "integer", 100, range=(50, 300, 25))
    p_match = Param("Maç", "integer", int(comeback["match_id"]), members=match_members)
    params = [p_team, p_player, p_side, p_ctx, p_map, p_min_shots, p_min_poss, p_match]

    # -- Takımlar ----------------------------------------------------------------------------
    kpis = Source("team_kpis.csv", "Takım künyesi", dimensions=("metric_order",), calcs=[
        Calc("kpi_satir", "KPI Satırı", f'IF {{Takım}} = "{ALL}" THEN [is_league] ELSE [team] = {{Takım}} END',
             "boolean")])
    profiles = Source("team_profiles.csv", "Takım profili", dimensions=("team_season_id",), calcs=[
        *team_block(), Calc("takimlar", "Takımlar", "NOT [is_league]", "boolean")],
        captions={"ortg": "Hücum reytingi (ORtg)", "drtg": "Savunma reytingi (DRtg)", "net": "Net reyting",
                  "pace": "Tempo", "team": "Takım", "team_short": "Takım (kısa)", "poss": "Hücum sayısı"},
        colors={"[none:Calculation_takim_vurgu:nk]": TEAM_COLORS})
    factors = Source("four_factors.csv", "Dört faktör", dimensions=("team_season_id", "factor_order"),
                     calcs=team_block(), captions={"z": "Ligden fark (z)", "value_text": "Değer",
                                                   "factor_short": "Faktör", "side": "Taraf"})
    starts = Source("possession_starts.csv", "Hücum başlangıçları", dimensions=("team_season_id", "start_order"), calcs=[
        *team_block(),
        Calc("bas_filtre", "Başlangıç Filtresi",
             f'[side] = {{Taraf}} AND [start_type] <> "inferred" AND [start_type] <> "jumpball" '
             f'AND [start_type] <> "unsportsmanlike_foul" AND ({{Takım}} = "{ALL}" OR [team] = {{Takım}})', "boolean"),
        Calc("ppp", "Hücum başına sayı", "SUM([pts]) / SUM([poss])", "real", "measure", "quantitative"),
        Calc("lig_ppp", "Lig ort.", "AVG([league_ppp])", "real", "measure", "quantitative"),
        Calc("ppp_fark", "Ligden iyi fark", 'IF {Taraf} = "Hücum" THEN [Calculation_ppp] - [Calculation_lig_ppp] '
             'ELSE [Calculation_lig_ppp] - [Calculation_ppp] END', "real", "measure", "quantitative"),
        Calc("ppp_renk", "Renk",
             f'IF {{Takım}} = "{ALL}" THEN "Lig" ELSEIF SUM([poss]) < 30 THEN "Az veri (<30 hücum)" '
             'ELSEIF [Calculation_ppp_fark] >= 0.05 THEN "Ligden iyi" ELSEIF [Calculation_ppp_fark] <= -0.05 '
             'THEN "Ligden kötü" ELSE "Lig düzeyinde" END', "string"),
    ], captions={"start": "Hücum başlangıcı", "poss": "Hücum sayısı", "share": "Pay"},
        colors={"Calculation_ppp_renk": {"Lig": SEC, "Az veri (<30 hücum)": LOW, "Ligden iyi": GOOD,
                                         "Ligden kötü": BAD, "Lig düzeyinde": MID}})

    t1 = Sheet("T1 Takım künyesi", kpis, rows=[F("header")], cols=[F("metric")], mark="Text",
               label=[F("value_text")], filters=[("true", F("Calculation_kpi_satir"))],
               sorts=[(F("metric"), F("metric_order", "sum", "qk"), "ASC", "columns")])
    t2 = Sheet("T2 Hücum × savunma", profiles, cols=[F("ortg", "avg", "qk")], rows=[F("drtg", "avg", "qk")],
               mark="Circle", color=F("Calculation_takim_vurgu"), label=[F("team_short")], label_cull=False,
               detail=[F("team"), F("Calculation_takim_tik")], filters=[("true", F("Calculation_takimlar"))],
               axes=[Axis(F("ortg", "avg", "qk"), "cols", 88, 126, title="Hücum reytingi (ORtg) → daha iyi", fmt=FMT_0),
                     Axis(F("drtg", "avg", "qk"), "rows", 88, 126, reverse=True,
                          title="Savunma reytingi (DRtg) ↑ daha iyi", fmt=FMT_0)],
               reflines=[RefLine(F("ortg", "avg", "qk"), round(league["ortg"], 1), "Lig ort."),
                         RefLine(F("drtg", "avg", "qk"), round(league["drtg"], 1), "Lig ort.")],
               formats={"[avg:ortg:qk]": FMT_1, "[avg:drtg:qk]": FMT_1}, mark_size=2.2,
               title=title("Hücum × savunma reytingi", "100 hücumda atılan (sağa) ve yenen (yukarı) sayı"),
               tooltip_runs=tip("<[none:team:nk]>", NL, "ORtg ", "<[avg:ortg:qk]>", "  ·  DRtg ", "<[avg:drtg:qk]>",
                                NL, "Tıklayın: bu takım bütün sayfalarda seçilir"))
    t3 = Sheet("T3 Dört faktör", factors, rows=[F("Calculation_takim_etiket")], cols=[F("side"), F("factor_short")],
               mark="Square", color=F("z", "sum", "qk"), color_range=(-2.0, 2.0), label=[F("value_text")],
               detail=[F("Calculation_takim_tik"), F("team")], tooltip=[F("league_text")],
               sorts=[(F("Calculation_takim_etiket"), F("net", "avg", "qk"), "DESC", "rows"),
                      (F("factor_short"), F("factor_order", "min", "ok"), "ASC", "columns")],
               title=title("Dört faktör", "Mavi: ligden iyi · Kırmızı: ligden kötü · Satırlar net reytinge göre"),
               tooltip_runs=tip("<[none:team:nk]>", NL, "<[none:side:nk]>", " · ", "<[none:factor_short:nk]>", ": ",
                                "<[none:value_text:nk]>", NL, "Lig: ", "<[none:league_text:nk]>"))
    t4 = Sheet("T4 Hücum başlangıçları", starts, rows=[F("start")], cols=[F("Calculation_ppp", "usr", "qk")],
               mark="Bar", color=F("Calculation_ppp_renk", "usr", "nk"), label=[F("Calculation_ppp", "usr", "qk")],
               tooltip=[F("poss", "sum", "qk"), F("Calculation_lig_ppp", "usr", "qk")],
               filters=[("true", F("Calculation_bas_filtre"))],
               sorts=[(F("start"), F("start_order", "min", "ok"), "ASC", "rows")],
               axes=[Axis(F("Calculation_ppp", "usr", "qk"), "cols", 0, 1.6, title="Hücum başına sayı", fmt=FMT_1)],
               formats={"[usr:Calculation_ppp:qk]": FMT_2, "[usr:Calculation_lig_ppp:qk]": FMT_2},
               title=title("Hücum nasıl başladı, kaç sayı getirdi?", None, [(" · ", T_SHEET), ("<Parameters.Taraf>", T_SHEET)]),
               tooltip_runs=tip("<[none:start:nk]>", NL, "Hücum başına sayı: ", "<[usr:Calculation_ppp:qk]>", NL,
                                "Lig: ", "<[usr:Calculation_lig_ppp:qk]>", NL, "Hücum: ", "<[sum:poss:qk]>"))

    # -- Şut Kalitesi ------------------------------------------------------------------------
    ctx = {"Hızlı hücum": "[transition]", "Set hücum": "NOT [transition] AND NOT [second_chance]",
           "İkinci şans": "[second_chance]", "Top kaybı sonrası": "[from_turnover]", "Clutch": "[clutch]"}
    ctx_case = f'CASE {{Şut Bağlamı}} WHEN "{ALL}" THEN TRUE ' + " ".join(
        f'WHEN "{k}" THEN {v}' for k, v in ctx.items()) + " END"
    shots = Source("shots.csv", "Şutlar", dimensions=("match_id", "week", "plot_x", "plot_y"), calcs=[
        Calc("sut_filtre", "Şut Filtresi",
             f'(IF {{Oyuncu}} <> "{ALL}" THEN [player] = {{Oyuncu}} ELSEIF {{Takım}} = "{ALL}" THEN TRUE '
             f'ELSEIF {{Taraf}} = "Hücum" THEN [team] = {{Takım}} ELSE [opponent] = {{Takım}} END) '
             f'AND [zone] <> "heave" AND [plot_y] <= 14 AND NOT [location_imputed] AND ({ctx_case})', "boolean"),
        Calc("hexx", "Hex X", "HEXBINX([plot_x] / 0.6, [plot_y] / 0.6) * 0.6", "real", "dimension", "quantitative"),
        Calc("hexy", "Hex Y", "HEXBINY([plot_x] / 0.6, [plot_y] / 0.6) * 0.6", "real", "dimension", "quantitative"),
        Calc("n", "Şut sayısı", "COUNT([xpts])", "integer", "measure", "quantitative"),
        Calc("isabet", "İsabet %", "AVG(IIF([made], 1, 0))", "real", "measure", "quantitative"),
        Calc("sayi_sut", "Sayı/şut", "SUM([pts]) / COUNT([xpts])", "real", "measure", "quantitative"),
        Calc("xpts_sut", "xPTS/şut", "SUM([xpts]) / COUNT([xpts])", "real", "measure", "quantitative"),
        Calc("renk", "Sayı/şut farkı",
             f'(IF {{Taraf}} = "Savunma" AND {{Takım}} <> "{ALL}" AND {{Oyuncu}} = "{ALL}" THEN -1 ELSE 1 END) * '
             f'(IF {{Harita Rengi}} = "Şut değeri (xPTS)" THEN [Calculation_xpts_sut] - {league["xpts_per_shot"]} '
             'ELSE (SUM([pts]) - SUM([xpts])) / (COUNT([xpts]) + 5) END)', "real", "measure", "quantitative"),
        Calc("goster", "Göster",
             f'IIF([Calculation_n] >= (IF {{Oyuncu}} <> "{ALL}" THEN 1 ELSEIF {{Takım}} <> "{ALL}" THEN 3 ELSE 8 END), 1, 0)',
             "integer", "measure", "quantitative"),
        Calc("baslik", "Başlık Seçim",
             f'(IF {{Oyuncu}} <> "{ALL}" THEN {{Oyuncu}} ELSEIF {{Takım}} <> "{ALL}" THEN {{Takım}} + '
             '(IF {Taraf} = "Hücum" THEN " · attığı şutlar" ELSE " · rakiplerinin şutları" END) '
             f'ELSE "Tüm lig" END) + (IF {{Şut Bağlamı}} <> "{ALL}" THEN " · " + {{Şut Bağlamı}} ELSE "" END)',
             "string"),
    ], image=("court.png", "Calculation_hexx", "Calculation_hexy", (0.0, 15.0, 0.0, 14.0)))
    pshoot = Source("player_shooting.csv", "Oyuncu şut", dimensions=("team_season_id", "player_id"), calcs=[
        *team_block(short=False), *player_block(top_case),
        Calc("min_sut", "Min Şut Filtresi", "[shots] >= {Min. şut}", "boolean"),
    ], captions={"xpts_per_shot": "Şut kalitesi (xPTS/şut)", "poe_per_shot": "Şut başarısı (POE/şut)",
                 "shots": "Şut", "player": "Oyuncu", "team": "Takım"},
        colors={"[none:Calculation_oyuncu_vurgu:nk]": PLAYER_COLORS})
    tshoot = Source("team_shooting.csv", "Takım şut", dimensions=("team_season_id",), calcs=[
        *team_block(),
        Calc("hx", "Aldığı şutların kalitesi", 'SUM(IF [side] = "offense" THEN [xpts_per_shot] END)', "real", "measure",
             "quantitative"),
        Calc("sx", "Rakibe verdiği şutların kalitesi", 'SUM(IF [side] = "defense" THEN [xpts_per_shot] END)', "real",
             "measure", "quantitative"),
        Calc("hp", "Hücum POE/şut", 'SUM(IF [side] = "offense" THEN [poe_per_shot] END)', "real", "measure",
             "quantitative"),
        Calc("ts_etiket", "Etiket", f'IF {{Takım}} = "{ALL}" OR [team] = {{Takım}} THEN [team_short] END', "string"),
        Calc("sp", "Rakip POE/şut", 'SUM(IF [side] = "defense" THEN [poe_per_shot] END)', "real", "measure",
             "quantitative"),
    ], colors={"[none:Calculation_takim_vurgu:nk]": TEAM_COLORS})

    s1 = Sheet("S1 Şut haritası", shots, cols=[F("Calculation_hexx", "none", "qk")],
               rows=[F("Calculation_hexy", "none", "qk")], mark="Circle", color=F("Calculation_renk", "usr", "qk"),
               color_range=(-0.35, 0.35), size=F("Calculation_n", "usr", "qk"),
               tooltip=[F("Calculation_isabet", "usr", "qk"), F("Calculation_sayi_sut", "usr", "qk"),
                        F("Calculation_xpts_sut", "usr", "qk")], detail=[F("Calculation_baslik", "attr", "nk")],
               filters=[("true", F("Calculation_sut_filtre")), ("min", F("Calculation_goster", "usr", "qk"), 1)],
               axes=[Axis(F("Calculation_hexx", "none", "qk"), "cols", 0, 15, hidden=True, title=""),
                     Axis(F("Calculation_hexy", "none", "qk"), "rows", 0, 14, hidden=True, title="")],
               formats={"[usr:Calculation_isabet:qk]": FMT_P1, "[usr:Calculation_sayi_sut:qk]": FMT_2,
                        "[usr:Calculation_xpts_sut:qk]": FMT_2, "[usr:Calculation_renk:qk]": FMT_S2}, mark_size=1.9,
               title=title("Şut haritası · ", "Daire büyüklüğü: şut sayısı · Renk: mavi iyi, kırmızı kötü",
                           [("<[attr:Calculation_baslik:nk]>", T_SHEET)]),
               tooltip_runs=tip("<[usr:Calculation_n:qk]>", " şut · isabet ", "<[usr:Calculation_isabet:qk]>", NL,
                                "Sayı/şut: ", "<[usr:Calculation_sayi_sut:qk]>", "  ·  xPTS/şut: ",
                                "<[usr:Calculation_xpts_sut:qk]>"))
    s2 = Sheet("S2 Oyuncu şut kalitesi", pshoot, cols=[F("xpts_per_shot", "avg", "qk")],
               rows=[F("poe_per_shot", "avg", "qk")], mark="Circle", color=F("Calculation_oyuncu_vurgu"),
               label=[F("Calculation_oyuncu_etiket")], label_cull=False, size=F("shots", "sum", "qk"),
               detail=[F("player"), F("team"), F("Calculation_oyuncu_tik"), F("noise"), F("interval_text")],
               filters=[("true", F("Calculation_min_sut"))],
               axes=[Axis(F("xpts_per_shot", "avg", "qk"), "cols", *x_xpts, title="Şut kalitesi (xPTS/şut) → iyi yerden atıyor"),
                     Axis(F("poe_per_shot", "avg", "qk"), "rows", title="POE/şut ↑ beklenenden fazla")],
               reflines=[RefLine(F("poe_per_shot", "avg", "qk"), 0.0),
                         RefLine(F("xpts_per_shot", "avg", "qk"), round(league["xpts_per_shot"], 3), "Lig ort.")],
               formats={"[avg:xpts_per_shot:qk]": FMT_2, "[avg:poe_per_shot:qk]": FMT_S2}, mark_size=0.6,
               title=title("Oyuncular: şut kalitesi × şut başarısı", None,
                           [("  (en az ", T_SHEET2), ("<Parameters.Min. şut>", T_SHEET2), (" şut)", T_SHEET2)]),
               tooltip_runs=tip("<[none:player:nk]>", NL, "<[none:team:nk]>", " · ", "<[sum:shots:qk]>", " şut", NL,
                                "xPTS/şut ", "<[avg:xpts_per_shot:qk]>", "  ·  POE/şut ", "<[avg:poe_per_shot:qk]>",
                                NL, "2 SE aralığı: ", "<[none:interval_text:nk]>", NL, "<[none:noise:nk]>"))
    s3 = Sheet("S3 Takım şut kalitesi", tshoot, cols=[F("Calculation_hx", "usr", "qk")],
               rows=[F("Calculation_sx", "usr", "qk")], mark="Circle", color=F("Calculation_takim_vurgu"),
               label=[F("Calculation_ts_etiket")], label_cull=False, detail=[F("team"), F("Calculation_takim_tik")],
               tooltip=[F("Calculation_hp", "usr", "qk"), F("Calculation_sp", "usr", "qk")],
               axes=[Axis(F("Calculation_hx", "usr", "qk"), "cols", 0.95, 1.12, title="Aldığı şutların kalitesi →"),
                     Axis(F("Calculation_sx", "usr", "qk"), "rows", 0.95, 1.12, reverse=True,
                          title="Verdiği şut kalitesi ↑ iyi")],
               reflines=[RefLine(F("Calculation_hx", "usr", "qk"), round(league["xpts_per_shot"], 3), "Lig ort."),
                         RefLine(F("Calculation_sx", "usr", "qk"), round(league["xpts_per_shot"], 3), "Lig ort.")],
               formats={"[usr:Calculation_hx:qk]": FMT_2, "[usr:Calculation_sx:qk]": FMT_2,
                        "[usr:Calculation_hp:qk]": FMT_S2, "[usr:Calculation_sp:qk]": FMT_S2}, mark_size=1.0,
               title=title("Takımlar: aldığı ve verdiği şutların kalitesi", "xPTS/şut · sağ üst: iki tarafta da iyi"),
               tooltip_runs=tip("<[none:team:nk]>", NL, "Aldığı şutlar ", "<[usr:Calculation_hx:qk]>",
                                " xPTS, sokma ", "<[usr:Calculation_hp:qk]>", NL, "Verdiği şutlar ",
                                "<[usr:Calculation_sx:qk]>", " xPTS, rakip sokma ", "<[usr:Calculation_sp:qk]>"))

    # -- Oyuncular ---------------------------------------------------------------------------
    pcard = Source("player_card.csv", "Oyuncu kartı", dimensions=("metric_order",), calcs=[
        Calc("profil_oyuncu", "Profil Oyuncusu", f'IF {{Oyuncu}} <> "{ALL}" THEN {{Oyuncu}} ELSE {top_case} END',
             "string"),
        Calc("profil_filtre", "Profil Filtresi", "[player] = [Calculation_profil_oyuncu]", "boolean")])
    pprof = Source("player_profiles.csv", "Oyuncu profili", dimensions=("team_season_id", "player_id"), calcs=[
        *team_block(short=False), *player_block(top_case),
        Calc("oyuncunun_takimi", "Oyuncunun Takımı", '{FIXED : MAX(IF [player] = {Oyuncu} THEN [team] END)}',
             "string"),
        Calc("gosterilen_takim", "Gösterilen Takım", shown_team, "string"),
        Calc("gosterilen_kisa", "Gösterilen Takım (kısa)", short_case, "string"),
        Calc("onoff_filtre", "OnOff Filtresi",
             "[team] = [Calculation_gosterilen_takim] AND [minutes] >= 50 AND NOT ISNULL([on_off])", "boolean"),
        Calc("onoff_renk", "OnOff Renk", f'IF [minutes] < {qmin} THEN "Az veri" ELSEIF [on_off] >= 0 '
                                         'THEN "Sahadayken daha iyi" ELSE "Sahadayken daha kötü" END', "string"),
        Calc("onoff_satir", "Oyuncu", 'IF [player] = {Oyuncu} THEN "▶ " + [Calculation_kisa_ad] '
                                      'ELSE [Calculation_kisa_ad] END', "string"),
    ], captions={"usg": "Kullanım (USG%)", "ts": "TS%", "on_off": "On/off farkı", "minutes": "Dakika",
                 "player": "Oyuncu", "team": "Takım"},
        colors={"[none:Calculation_oyuncu_vurgu:nk]": PLAYER_COLORS,
                "[none:Calculation_onoff_renk:nk]": {"Az veri": LOW, "Sahadayken daha iyi": GOOD,
                                                     "Sahadayken daha kötü": BAD}})
    pct = Source("player_percentiles.csv", "Oyuncu yüzdelikleri",
                 dimensions=("team_season_id", "player_id", "metric_order"), calcs=[
        Calc("profil_oyuncu", "Profil Oyuncusu", f'IF {{Oyuncu}} <> "{ALL}" THEN {{Oyuncu}} ELSE {top_case} END',
             "string"),
        Calc("profil_filtre", "Profil Filtresi", "[player] = [Calculation_profil_oyuncu]", "boolean")],
        captions={"pct": "Yüzdelik", "label": "Metrik", "group": "Grup", "value_text": "Değer"},
        colors={"[none:pct_class:nk]": {"Üst %20": GOOD, "60–80": GOOD_L, "40–60": MID, "20–40": BAD_L,
                                        "Alt %20": BAD, "Stil (iyi/kötü değil)": "#a7b0ba"}})

    o1 = Sheet("O1 Oyuncu kartı", pcard, cols=[F("metric")], mark="Text", label=[F("value_text")],
               detail=[F("player", "attr", "nk"), F("team", "attr", "nk"), F("sub", "attr", "nk")],
               filters=[("true", F("Calculation_profil_filtre"))],
               sorts=[(F("metric"), F("metric_order", "sum", "qk"), "ASC", "columns")],
               title=[("<[attr:player:nk]>", {"fontname": "Tableau Semibold", "fontsize": "16", "fontcolor": INK}),
                      ("  ·  ", T_SHEET2), ("<[attr:team:nk]>", T_SHEET2), NL, ("<[attr:sub:nk]>", T_SHEET2)])
    o2 = Sheet("O2 Yüzdelik profil", pct, rows=[F("group"), F("label")], cols=[F("pct", "avg", "qk")], mark="Bar",
               color=F("pct_class"), label=[F("value_text")], filters=[("true", F("Calculation_profil_filtre"))],
               sorts=[(F("group"), F("metric_order", "min", "ok"), "ASC", "rows"),
                      (F("label"), F("metric_order", "min", "ok"), "ASC", "rows")],
               axes=[Axis(F("pct", "avg", "qk"), "cols", 0, 100, title="Yüzdelik (50 = lig ortancası)", fmt=FMT_0)],
               reflines=[RefLine(F("pct", "avg", "qk"), 50.0)], formats={"[avg:pct:qk]": FMT_0},
               title=title("Ligdeki yeri", f"{qmin}+ dakika oynayanlar arasında yüzdelik · gri çubuklar oyun stilidir"),
               tooltip_runs=tip("<[none:label:nk]>", ": ", "<[none:value_text:nk]>", NL, "Yüzdelik: ", "<[avg:pct:qk]>"))
    o3 = Sheet("O3 Kullanım × TS", pprof, cols=[F("usg", "avg", "qk")], rows=[F("ts", "avg", "qk")], mark="Circle",
               color=F("Calculation_oyuncu_vurgu"), label=[F("Calculation_oyuncu_etiket")], label_cull=False,
               size=F("minutes", "sum", "qk"), detail=[F("player"), F("team"), F("Calculation_oyuncu_tik")],
               filters=[("member", F("qualified"), True)],
               axes=[Axis(F("usg", "avg", "qk"), "cols", 0.06, 0.36, title="Kullanım (USG%) → daha çok top", fmt=FMT_P0),
                     Axis(F("ts", "avg", "qk"), "rows", *y_ts, title="TS% ↑ daha verimli", fmt=FMT_P0)],
               reflines=[RefLine(F("usg", "avg", "qk"), 0.2, "Ort. kullanım"),
                         RefLine(F("ts", "avg", "qk"), round(league["ts"], 3), "Lig TS%")],
               formats={"[avg:usg:qk]": FMT_P1, "[avg:ts:qk]": FMT_P1}, mark_size=0.6,
               title=title("Kullanım × verimlilik", "Sağ üst: çok top kullanıp verimli kalanlar · tıklayın"),
               tooltip_runs=tip("<[none:player:nk]>", NL, "<[none:team:nk]>", NL, "USG% ", "<[avg:usg:qk]>",
                                "  ·  TS% ", "<[avg:ts:qk]>", NL, "<[sum:minutes:qk]>", " dakika"))
    o4 = Sheet("O4 On/off", pprof, rows=[F("Calculation_onoff_satir")], cols=[F("on_off", "sum", "qk")], mark="Bar",
               color=F("Calculation_onoff_renk"), label=[F("on_off_text")],
               detail=[F("player"), F("team"), F("Calculation_oyuncu_tik"), F("Calculation_gosterilen_kisa", "attr", "nk")],
               tooltip=[F("minutes", "sum", "qk")], filters=[("true", F("Calculation_onoff_filtre"))],
               sorts=[(F("Calculation_onoff_satir"), F("on_off", "sum", "qk"), "DESC", "rows")],
               axes=[Axis(F("on_off", "sum", "qk"), "cols", title="Sahadayken − kenardayken net reyting", fmt=FMT_0)],
               formats={"[sum:on_off:qk]": FMT_1},
               title=title("On/off · ", "Tek oyuncu için çok gürültülüdür; yönüne bakın",
                           [("<[attr:Calculation_gosterilen_kisa:nk]>", T_SHEET)]),
               tooltip_runs=tip("<[none:player:nk]>", NL, "On/off: ", "<[none:on_off_text:nk]>", NL,
                                "<[sum:minutes:qk]>", " dakika"))

    # -- İkililer ----------------------------------------------------------------------------
    pairs = Source("pairs_long.csv", "İkililer", calcs=[
        Calc("oyuncunun_takimi", "Oyuncunun Takımı",
             '{FIXED : MAX(IF [player_a] = {Oyuncu} OR [player_b] = {Oyuncu} THEN [team] END)}', "string"),
        Calc("gosterilen_takim", "Gösterilen Takım", shown_team, "string"),
        Calc("gosterilen_kisa", "Gösterilen Takım (kısa)", short_case, "string"),
        Calc("ikili_filtre", "İkili Filtresi",
             f'[team] = [Calculation_gosterilen_takim] AND [poss] >= {{Min. hücum (ikili)}} AND '
             f'({{Oyuncu}} = "{ALL}" OR [player_a] = {{Oyuncu}} OR [player_b] = {{Oyuncu}})', "boolean"),
    ], captions={"value": "Net reyting", "kind": "Durum", "pair": "İkili", "sample": "Örneklem"},
        colors={"[none:kind:nk]": {"Birlikte": INK, "Biri kenardayken": "#a7b0ba"}})
    assists = Source("assist_pairs.csv", "Asist ağı", dimensions=("team_season_id", "passer_id", "scorer_id"), calcs=[
        Calc("oyuncunun_takimi", "Oyuncunun Takımı",
             '{FIXED : MAX(IF [passer] = {Oyuncu} OR [scorer] = {Oyuncu} THEN [team] END)}', "string"),
        Calc("gosterilen_takim", "Gösterilen Takım", shown_team, "string"),
        Calc("gosterilen_kisa", "Gösterilen Takım (kısa)", short_case, "string"),
        Calc("asist_filtre", "Asist Filtresi", "[team] = [Calculation_gosterilen_takim]", "boolean"),
        Calc("pas_veren", "Pas veren", 'IF [passer] = {Oyuncu} THEN "▶ " + [passer_short] ELSE [passer_short] END',
             "string"),
        Calc("sayi_atan", "Sayı atan", 'IF [scorer] = {Oyuncu} THEN "▶ " + [scorer_short] ELSE [scorer_short] END',
             "string"),
        Calc("pas_tik", "Pas Veren Tık", f'IF [passer] = {{Oyuncu}} THEN "{ALL}" ELSE [passer] END', "string"),
    ], captions={"assists": "Asist", "pts": "Sayı"})
    i1 = Sheet("İ1 İkili net reyting", pairs, rows=[F("pair"), F("sample")], cols=[F("value", "avg", "qk")],
               mark="Circle", color=F("kind"), detail=[F("team"), F("Calculation_gosterilen_kisa", "attr", "nk")],
               filters=[("true", F("Calculation_ikili_filtre"))],
               sorts=[(F("pair"), F("net", "avg", "qk"), "DESC", "rows")],
               axes=[Axis(F("value", "avg", "qk"), "cols", title="Net reyting (100 hücumda sayı farkı)", fmt=FMT_0)],
               reflines=[RefLine(F("value", "avg", "qk"), 0.0)], formats={"[avg:value:qk]": FMT_1}, mark_size=0.8,
               title=title("İkililer · ", "Siyah: ikisi birlikteyken · Gri: biri kenardayken · ± şans payıdır",
                           [("<[attr:Calculation_gosterilen_kisa:nk]>", T_SHEET)]),
               tooltip_runs=tip("<[none:pair:nk]>", NL, "<[none:kind:nk]>", ": ", "<[avg:value:qk]>", NL,
                                "<[none:sample:nk]>"))
    i2 = Sheet("İ2 Asist ağı", assists, rows=[F("Calculation_pas_veren")], cols=[F("Calculation_sayi_atan")],
               mark="Square", color=F("assists", "sum", "qk"), color_seq="blue_10_0", label=[F("assists", "sum", "qk")],
               detail=[F("Calculation_pas_tik"), F("team"), F("Calculation_gosterilen_kisa", "attr", "nk"),
                       F("passer"), F("scorer")],
               tooltip=[F("pts", "sum", "qk")], filters=[("true", F("Calculation_asist_filtre"))],
               sorts=[(F("Calculation_pas_veren"), F("assists", "sum", "qk"), "DESC", "rows"),
                      (F("Calculation_sayi_atan"), F("assists", "sum", "qk"), "DESC", "columns")],
               title=title("Asist ağı · ", "Satır: pası veren · Sütun: sayıyı atan · tıklayın: oyuncuyu seçer",
                           [("<[attr:Calculation_gosterilen_kisa:nk]>", T_SHEET)]),
               tooltip_runs=tip("<[none:passer:nk]>", " → ", "<[none:scorer:nk]>", NL, "<[sum:assists:qk]>", " asist · ",
                                "<[sum:pts:qk]>", " sayı"))

    # -- Maçlar ------------------------------------------------------------------------------
    timeline = Source("score_timeline.csv", "Skor akışı",
                      dimensions=("match_id", "seq", "action_number", "period_ord", "team_season_id", "player_id",
                                  "week", "home_id", "away_id", "minute", "elapsed_s", "margin_home"),
                      calcs=[Calc("mac_filtre", "Maç Filtresi", "[match_id] = {Maç}", "boolean")],
                      captions={"minute": "Dakika", "margin_home": "Skor farkı"})
    summary = Source("match_summary.csv", "Maç özeti", dimensions=("match_id", "metric_order"),
                     calcs=[Calc("mac_filtre", "Maç Filtresi", "[match_id] = {Maç}", "boolean")])
    flow = Source("game_flow.csv", "Maç akışı", dimensions=("match_id", "team_season_id", "opponent_id", "week"), calcs=[
        *team_block(),
        Calc("geri_donus", "Geri Dönüş Filtresi", "[won] AND [largest_deficit] >= 8", "boolean"),
        Calc("mac_vurgu", "Maç Vurgu",
             f'IF [match_id] = {{Maç}} THEN "Seçili maç" ELSEIF {{Takım}} <> "{ALL}" AND ([team] = {{Takım}} '
             'OR [opponent] = {Takım}) THEN "Seçili takımın maçı" ELSE "Diğer" END', "string"),
    ], captions={"largest_deficit": "En çok geride", "comeback": "Maç"},
        colors={"[none:Calculation_mac_vurgu:nk]": {"Seçili maç": ACCENT, "Seçili takımın maçı": SEC, "Diğer": GREY}})
    runs = Source("runs.csv", "Seriler", dimensions=("match_id", "team_season_id", "start_period"),
                  calcs=[Calc("mac_filtre", "Maç Filtresi", "[match_id] = {Maç}", "boolean")])
    clutch = Source("clutch_team.csv", "Clutch takım", dimensions=("team_season_id",), calcs=[
        *team_block(),
        Calc("clutch_renk", "Clutch Renk", f'IF [team] = {{Takım}} THEN "Seçili takım" ELSEIF [poss] < 20 '
                                           f'THEN "Az veri (<20 hücum)" ELSEIF {{Takım}} = "{ALL}" THEN "{ALL}" '
                                           'ELSE "Diğer" END', "string"),
    ], captions={"net": "Clutch net reyting"},
        colors={"[none:Calculation_clutch_renk:nk]": {"Seçili takım": ACCENT, "Az veri (<20 hücum)": LOW, ALL: SEC,
                                                       "Diğer": GREY}})
    m1 = Sheet("M1 Skor farkı", timeline, cols=[F("minute", "none", "qk")], rows=[F("margin_home", "none", "qk")],
               mark="Line", aggregate=False, line_step=True, tooltip=[F("clock_label"), F("score_label"), F("player")],
               detail=[F("home_short", "attr", "nk"), F("away_short", "attr", "nk"), F("home_score", "max", "qk"),
                       F("away_score", "max", "qk"), F("date", "attr", "ok")],
               filters=[("true", F("Calculation_mac_filtre"))],
               axes=[Axis(F("minute", "none", "qk"), "cols", title="Dakika", fmt=FMT_0),
                     Axis(F("margin_home", "none", "qk"), "rows", title="Fark (ev sahibi − deplasman)", fmt=FMT_0)],
               reflines=[RefLine(F("margin_home", "none", "qk"), 0.0), RefLine(F("minute", "none", "qk"), 10.0, "Ç2"),
                         RefLine(F("minute", "none", "qk"), 20.0, "Ç3"), RefLine(F("minute", "none", "qk"), 30.0, "Ç4")],
               title=title("", "↑ ev sahibi önde · ↓ deplasman önde",
                           [("<[attr:home_short:nk]>", T_SHEET), (" ", T_SHEET), ("<[max:home_score:qk]>", T_SHEET),
                            ("–", T_SHEET), ("<[max:away_score:qk]>", T_SHEET), (" ", T_SHEET),
                            ("<[attr:away_short:nk]>", T_SHEET), (" · ", T_SHEET2), ("<[attr:date:ok]>", T_SHEET2)]),
               tooltip_runs=tip("<[none:clock_label:nk]>", NL, "<[none:player:nk]>", NL, "Skor ",
                                "<[none:score_label:nk]>"))
    m2 = Sheet("M2 Maç özeti", summary, rows=[F("team"), F("result")], cols=[F("metric")], mark="Text",
               label=[F("value_text")], filters=[("true", F("Calculation_mac_filtre"))],
               sorts=[(F("metric"), F("metric_order", "sum", "qk"), "ASC", "columns")])
    m3 = Sheet("M3 Seriler", runs, rows=[F("run_time"), F("team_short"), F("run_text"), F("margin_change")],
               mark="Text", label=[F("duration_s", "sum", "qk")], filters=[("true", F("Calculation_mac_filtre"))],
               sorts=[(F("run_time"), F("start_s", "min", "qk"), "ASC", "rows")],
               formats={"[sum:duration_s:qk]": 'n#,##0" sn";-#,##0" sn"'},
               title=title("8 sayı ve üstü seriler", "Fark, seriyi yapan takımın gözünden · süre saniye"))
    m4 = Sheet("M4 Geri dönüşler", flow, rows=[F("comeback")], cols=[F("largest_deficit", "sum", "qk")], mark="Bar",
               color=F("Calculation_mac_vurgu"), label=[F("largest_deficit", "sum", "qk")],
               detail=[F("match_id", "none", "ok"), F("team")], filters=[("true", F("Calculation_geri_donus"))],
               sorts=[(F("comeback"), F("largest_deficit", "sum", "qk"), "DESC", "rows")],
               axes=[Axis(F("largest_deficit", "sum", "qk"), "cols", title="Kazanırken en çok kaç sayı geride kaldı",
                          fmt=FMT_0)],
               title=title("En büyük geri dönüşler", "Tıklayın: maç akışı sağda açılır"),
               tooltip_runs=tip("<[none:comeback:nk]>", NL, "En çok ", "<[sum:largest_deficit:qk]>", " sayı gerideydi"))
    m5 = Sheet("M5 Clutch", clutch, rows=[F("Calculation_takim_etiket")], cols=[F("net", "sum", "qk")], mark="Bar",
               color=F("Calculation_clutch_renk"), label=[F("label")], detail=[F("team"), F("Calculation_takim_tik")],
               sorts=[(F("Calculation_takim_etiket"), F("net", "sum", "qk"), "DESC", "rows")],
               axes=[Axis(F("net", "sum", "qk"), "cols", title="Clutch net reyting", fmt=FMT_0)],
               reflines=[RefLine(F("net", "sum", "qk"), 0.0)],
               title=title("Clutch: son 5 dakika, fark ≤ 5", "Açık gri: 20 hücumdan az · sıralama şansa açıktır"),
               tooltip_runs=tip("<[none:team:nk]>", NL, "<[none:label:nk]>"))

    sheets = [t1, t2, t3, t4, s1, s2, s3, o1, o2, o3, o4, i1, i2, m1, m2, m3, m4, m5]
    sources = [kpis, profiles, factors, starts, shots, pshoot, tshoot, pcard, pprof, pct, pairs, assists,
               timeline, summary, flow, runs, clutch]

    # -- dashboards --------------------------------------------------------------------------
    def controls(*ps, widths, x=16, y=72):
        out, cx = [], x
        for p, w in zip(ps, widths):
            out.append(Zone("param", cx, y, w, 42, param=p, mode="slider" if p.range else "compact"))
            cx += w + 12
        return out

    sub = f"TBL {season} · {n_teams} takım · {n_games} maç ({weeks[0]}–{weeks[1]}. hafta)"
    dashboards = [
        Dashboard("Takımlar", frame(
            "Kim hücumda, kim savunmada iyi?", sub + " · Reytingler 100 hücum başına sayıdır",
            "Sağa gidildikçe hücum, yukarı gidildikçe savunma daha iyi. Bir takıma tıklayın, seçim tüm sayfalara geçer; "
            f"tekrar tıklayınca kalkar. {games_txt} maçta reytinglerde ±{rating_se} puan şans payı vardır.", footer)
            + controls(p_team, p_side, widths=[240, 160]) + [
            Zone("sheet", 16, 148, 760, 58, sheet=t1.name, show_title=False),
            Zone("sheet", 16, 212, 540, 562, sheet=t2.name),
            Zone("sheet", 568, 212, 616, 380, sheet=t3.name),
            Zone("sheet", 568, 598, 616, 176, sheet=t4.name)]),
        Dashboard("Şut Kalitesi", frame(
            "İyi şut mu alıyorlar, iyi mi atıyorlar?",
            "xPTS: şutun yerine ve bağlamına göre beklenen sayı · POE: atılan − beklenen sayı",
            "Haritada daire büyüklüğü şut sayısı, renk şut değeri ya da başarısıdır (mavi iyi). Az şutlu altıgenler sıfıra "
            "çekilir. Sağdaki grafiklerde bir oyuncuya ya da takıma tıklayın.", footer)
            + controls(p_team, p_player, p_side, p_ctx, p_map, p_min_shots, widths=[170, 190, 150, 150, 180, 140]) + [
            Zone("sheet", 16, 148, 540, 590, sheet=s1.name, map_controls=False),
            Zone("legend", 16, 740, 300, 36, sheet=s1.name, legend_field=F("Calculation_renk", "usr", "qk")),
            Zone("sheet", 568, 148, 616, 330, sheet=s2.name),
            Zone("sheet", 568, 484, 616, 290, sheet=s3.name)]),
        Dashboard("Oyuncular", frame(
            "Oyuncu ligde nerede duruyor?", f"Yüzdelikler {qmin}+ dakika oynayan oyuncular arasında · 0 en alt, 100 en üst",
            "Soldaki çubuklar oyuncunun her metrikte ligdeki yüzdelik dilimi (50 = ortanca). Sağdaki grafikte bir oyuncuya "
            "tıklayın. On/off tek oyuncu için çok gürültülüdür.", footer)
            + controls(p_team, p_player, widths=[240, 300]) + [
            Zone("sheet", 16, 148, 540, 100, sheet=o1.name),
            Zone("sheet", 16, 252, 540, 522, sheet=o2.name),
            Zone("sheet", 568, 148, 616, 330, sheet=o3.name),
            Zone("sheet", 568, 484, 616, 290, sheet=o4.name)]),
        Dashboard("İkililer", frame(
            "Hangi ikililer birlikte daha iyi?", "Aynı anda sahadayken net reyting ve biri kenardayken · asist ağı",
            "Siyah nokta grinin sağındaysa ikili birlikte daha iyi. 100 hücumda ±15 civarı şans payı vardır. Bir oyuncu "
            "seçince yalnız onun ikilileri kalır; asist ağında bir satıra tıklayın.", footer)
            + controls(p_team, p_player, p_min_poss, widths=[240, 300, 200]) + [
            Zone("sheet", 16, 148, 600, 626, sheet=i1.name, fit="fit-width"),
            Zone("sheet", 632, 148, 552, 626, sheet=i2.name)]),
        Dashboard("Maçlar", frame(
            "Maç nasıl döndü?", "Skor farkı dakika dakika · 8+ sayılık seriler · geri dönüşler · son 5 dakika (clutch)",
            "Çizgi 0'ın üstündeyse ev sahibi önde. Soldaki listeden bir maça tıklayın ya da Maç listesinden seçin. "
            "Clutch'ta takım başına çok az hücum vardır; yönü okuyun.", footer)
            + controls(p_match, p_team, widths=[380, 240]) + [
            Zone("sheet", 16, 148, 360, 250, sheet=m4.name),
            Zone("sheet", 16, 404, 360, 370, sheet=m5.name),
            Zone("sheet", 392, 148, 792, 330, sheet=m1.name),
            Zone("sheet", 392, 484, 792, 100, sheet=m2.name, show_title=False),
            Zone("sheet", 392, 590, 792, 184, sheet=m3.name)]),
        Dashboard("Yöntem", frame("Veri, yöntem ve sözlük", "Bu çalışma nasıl yapıldı, sayılar nasıl okunmalı", None,
                                  footer) + [
            Zone("text", 16, 80, 370, 690, runs=method_veri(n_games, weeks, season, n_teams, span)),
            Zone("text", 410, 80, 370, 690, runs=method_sozluk()),
            Zone("text", 804, 80, 380, 690, runs=method_orneklem(games_txt, rating_se))]),
    ]
    actions = []

    def click(dash: str, sheet: Sheet, field: str, param: Param, caption: str) -> None:
        actions.append(ParamAction(f"{dash} · {sheet.name} · {caption}", dash, sheet.name, F(field), param))

    for sh in (t2, t3):
        click("Takımlar", sh, "Calculation_takim_tik", p_team, "takım")
        click("Takımlar", sh, "Calculation_sabit_tumu", p_player, "oyuncuyu sıfırla")
    click("Şut Kalitesi", s3, "Calculation_takim_tik", p_team, "takım")
    click("Şut Kalitesi", s3, "Calculation_sabit_tumu", p_player, "oyuncuyu sıfırla")
    for dash, sh in (("Şut Kalitesi", s2), ("Oyuncular", o3), ("Oyuncular", o4)):
        click(dash, sh, "Calculation_oyuncu_tik", p_player, "oyuncu")
        click(dash, sh, "team", p_team, "oyuncunun takımı")
    click("İkililer", i2, "Calculation_pas_tik", p_player, "pası veren")
    click("İkililer", i2, "team", p_team, "takım")
    click("Maçlar", m4, "match_id", p_match, "maç")
    click("Maçlar", m5, "Calculation_takim_tik", p_team, "takım")
    return Workbook(d, params, sources, sheets, dashboards, actions, start="Takımlar")


def method_veri(n_games: int, weeks, season: str, n_teams: int, span: str) -> list[tuple[str, dict]]:
    return [("Veri", T_H), NL, NL, (
        f"TBL (Türkiye Basketbol Ligi) {season} {span}, {weeks[0]}–{weeks[1]}. hafta, {n_games} maç, {n_teams} takım. "
        "Kaynak: Türkiye Basketbol Federasyonu ve FIBA LiveStats'in kamuya açık maç verisi (oyun oyun kayıt, şut "
        "konumları, box score).", T_BODY), NL, NL, (
        "Hücumlar her olaydan tek tek sayıldı (Dean Oliver tanımı: hücum ribaundu hücumu uzatır). Reytingler 100 "
        "hücum başına sayıdır. Oyuncu oranları oyuncu sahadayken takımın yaptıklarına göre hesaplandı.", T_BODY), NL, NL, (
        "Veri resmi box score ile ve FIBA LiveStats'in kendi maç özetleriyle (en büyük fark, seri, liderlik değişimi, "
        "eşitlik, önde geçen süre) birebir doğrulandı. Konumu tahmin edilen tip-in'ler ve yarı sahanın ötesinden "
        "atılan şutlar haritada gösterilmez.", T_BODY)]


def method_sozluk() -> list[tuple[str, dict]]:
    items = [("ORtg / DRtg / Net", "100 hücumda atılan / yenen sayı ve farkı."),
             ("Tempo", "40 dakikada hücum sayısı."),
             ("eFG%", "Üçlüğü 1,5 sayan isabet oranı."), ("TS%", "Serbest atışları da sayan şut verimi."),
             ("USG%", "Oyuncu sahadayken takımın oyunlarının ne kadarını bitirdiği (ortalama %20)."),
             ("Dört faktör", "Şut verimi, top kaybı, ribaund ve serbest atış: maçı belirleyen dört alan."),
             ("xPTS", "Şutun yerine, açısına, türüne ve bağlamına göre beklenen sayısı; atan oyuncu modele girmez."),
             ("POE", "Atılan sayı − beklenen sayı: beklenenin üstünde atma."),
             ("On/off", "Oyuncu sahadayken ve kenardayken takımın net reyting farkı."),
             ("Clutch", "4. çeyreğin ya da uzatmanın son 5 dakikası, fark ≤ 5."),
             ("Seri", "Bir takımın rakibi sayı atmadan üst üste attığı sayılar.")]
    runs = [("Sözlük", T_H), NL, NL]
    for k, v in items:
        runs += [(k + ": ", {"fontname": "Tableau Semibold", "fontsize": "10", "fontcolor": INK}), (v, T_BODY), NL]
    return runs


def method_orneklem(games_txt: str, rating_se: int) -> list[tuple[str, dict]]:
    return [("Örneklem uyarısı", T_H), NL, NL, (
        f"Takım başına {games_txt} maç oynandı. Bir takımın reytingi ±{rating_se}, 100 hücumluk bir ikilinin net "
        "reytingi ±15 kadar şansla oynayabilir (1 standart hata). Clutch'ta takım başına az hücum vardır.", T_BODY), NL, NL, (
        "Bu yüzden oranların yanında hücum, şut ya da dakika sayısı yazar; ipuçlarında şans payı verilir. Şut "
        "haritasında az şutlu altıgenler sıfıra çekilir.", T_BODY), NL, NL, (
        "xPTS modeli: lojistik regresyon, maç bazında 5 katlı çapraz doğrulama. Log loss 0,647 (bölge isabet "
        "oranıyla 0,659). Modelin toplam beklentisi atılan sayıya %0,2 yakın.", T_BODY)]
