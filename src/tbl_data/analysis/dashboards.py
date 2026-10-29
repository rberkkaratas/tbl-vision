"""What the Tableau workbook shows: parameters, data sources with their calculated fields,
worksheets, dashboards and click actions (written to XML by workbook.py).

Design: one main team (Ana takım, chosen on Anasayfa) shapes every page and lends its colour
to the header; grey cards on a neutral canvas, the team colour as the accent. Parameters (Ana
takım, Rakip, Oyuncu, Maç, ...) connect every data source: each source has calculated fields
that read them, and clicking a team, a player or a game sets them. Text a viewer reads is
pre-formatted in Turkish (presentation.py) and describes rather than judges; the workbook
locale is tr_TR so axis numbers use decimal commas.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from .assets import PALETTE
from .glossary import term
from .presentation import num
from .workbook import Axis, Calc, Dashboard, F, GoTo, Param, ParamAction, RefLine, Sheet, Source, Workbook, Zone

INK, SEC, RULE = "#1b2430", "#5b6673", "#e3e6ea"
ACCENT, ACCENT_LIGHT, GREY, LOW = "#e8590c", "#f6b48a", "#b4bcc5", "#e1e5ea"
GOOD, GOOD_L, MID, BAD_L, BAD = "#2166ac", "#92c5de", "#dcdcdc", "#f4a582", "#b2182b"
ALL = "Tümü"
TOP_COMEBACKS = 8  # rows that fit the comeback chart at a readable size
NAV = ["Anasayfa", "Rakip Analizi", "Takımlar", "Şut Kalitesi", "Oyuncular", "İkililer", "Maçlar", "Güven", "Yöntem"]
NAV_TITLE = {"Şut Kalitesi": "Şutlar", "Rakip Analizi": "Rakip"}  # short button labels

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
TERM_TIP = ("<[none:term_tr:nk]>", NL, "<[none:term_desc:nk]>")  # a row's metric and its definition (glossary.py)
TERM_FIELDS = [F("term_tr"), F("term_desc")]

FMT_0 = "n#,##0;-#,##0"
FMT_1 = "n#,##0.0;-#,##0.0"
FMT_2 = "n#,##0.00;-#,##0.00"
FMT_S2 = "*+#,##0.00;-#,##0.00;0.00"
FMT_P0 = "p0%"
FMT_P1 = "p0.0%"

# the theme: the chosen team's marks in its own colour (assets.py), the opponent in ink. A themed value
# carries the team's short name ("Ana takım · Ormanspor"), and every team gets its colour for that value.
THEME: dict[str, dict[str, str]] = {"color": {}, "light": {}}  # team short name -> colour / tint (design fills it)


def tag(label: str) -> str:
    """Formula: the label followed by the row's team, the key a themed colour map uses."""
    return f'"{label} · " + [team_short]'


def tinted(label: str, light: bool = False) -> dict[str, str]:
    return {f"{label} · {t}": c for t, c in THEME["light" if light else "color"].items()}


def team_colors() -> dict[str, str]:
    return {**tinted("Ana takım"), "Rakip": INK, "Diğer": GREY}


def player_colors() -> dict[str, str]:
    return {**tinted("Seçili oyuncu"), **tinted("Seçili takım", light=True), ALL: SEC, "Diğer": GREY}
NEXT = "Sıradaki rakip"  # Rakip parameter: the chosen team's next opponent in the fixture
LAST = 0  # Maç parameter: the chosen team's last game
DEFAULT_TEAM = "OGM ORMANSPOR"  # Ana takım when the workbook opens (else the strongest team)
# formula tokens, replaced once the fixture is known: the opponent and the game actually shown
RAKIP, MAC = "[[RAKIP]]", "[[MAC]]"
# the team a page shows: Ana takım or Rakip (Görünüm parameter)
GOSTER = '(IF {Görünüm} = "Rakip" THEN [[RAKIP]] ELSE {Ana takım} END)'
GUVEN_H = 1100  # taller than the rest: roster, table and reliability on top, the method checks below


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


def defs(*keys: str) -> list[tuple[str, dict]]:
    """Definition lines for a chart's measures, under its tooltip."""
    return [x for k in keys for x in (NL, (term(k), TIP))]


def team_block(short: bool = True) -> list[Calc]:
    out = [
        Calc("takim_filtre", "Takım Filtresi", f'{{Ana takım}} = "{ALL}" OR [team] = {{Ana takım}}', "boolean"),
        Calc("takim_vurgu", "Takım Vurgu", f'IF [team] = {{Ana takım}} THEN {tag("Ana takım")} ELSEIF [team] = {RAKIP} '
                                           'THEN "Rakip" ELSE "Diğer" END', "string"),
        # a click picks the team to compare with; clicking the current one goes back to the next opponent
        Calc("takim_tik", "Takım Tık", f'IF [team] = {RAKIP} OR [team] = {{Ana takım}} THEN "{NEXT}" ELSE [team] END',
             "string"),
        Calc("sabit_tumu", "Sabit Tümü", f'"{ALL}"', "string"),
    ]
    if short:
        out.append(Calc("takim_etiket", "Takım Etiketi",
                        'IF [team] = {Ana takım} THEN "▶ " + [team_short] ELSE [team_short] END', "string"))
    return out


def player_block(top_player_case: str) -> list[Calc]:
    return [
        Calc("oyuncu_tik", "Oyuncu Tık", f'IF [player] = {{Oyuncu}} THEN "{ALL}" ELSE [player] END', "string"),
        Calc("oyuncu_vurgu", "Oyuncu Vurgu",
             f'IF [player] = {{Oyuncu}} THEN {tag("Seçili oyuncu")} ELSEIF [team] = {GOSTER} THEN {tag("Seçili takım")} '
             'ELSE "Diğer" END',
             "string"),
        # drops the club a mid-season transfer carries: "Tayfun Erülkü (Yalovaspor)" -> "T. Erülkü"
        Calc("kisa_ad", "Kısa Ad", 'LEFT([player], 1) + ". " + SPLIT(SPLIT([player], " (", 1), " ", -1)', "string"),
        Calc("oyuncu_etiket", "Oyuncu Etiketi",
             f'IF [player] = {{Oyuncu}} OR [team] = {GOSTER} THEN [Calculation_kisa_ad] END', "string"),
        Calc("serit_ad", "Oyuncu", 'IF [player] = {Oyuncu} THEN "▶ " + [Calculation_kisa_ad] ELSE [Calculation_kisa_ad] END',
             "string"),
        Calc("profil_oyuncu", "Profil Oyuncusu",
             f'IF {{Oyuncu}} <> "{ALL}" THEN {{Oyuncu}} ELSE {top_player_case} END', "string"),
    ]


def frame(heading: str, subtitle: str, note: str | None, footer: str, height: int = 800) -> list[Zone]:
    zones = [Zone("blank", 0, 0, 1200, 4, background=ACCENT),
             Zone("text", 16, 10, 1168, 32, runs=[(heading, T_TITLE)]),
             Zone("text", 16, 42, 900, 20, runs=[(subtitle, T_SUB)]),
             Zone("blank", 16, 66, 1168, 1, background=RULE),
             Zone("text", 16, height - 22, 1168, 18, runs=[(footer, T_FOOT)])]
    if note:
        zones.append(Zone("text", 16, 116, 1168, 30, runs=[("Nasıl okunur · ", T_NOTE_B), (note, T_NOTE)]))
    return zones


NAV_H = 34  # the page bar on top of every dashboard; everything else sits below it
STRIP_H, STRIP_EXTRA = 46, 12  # roster strip (player names over minutes per game) and how far it pushes the views


LOGO_W = 52  # the team logo left of each page title
LOGO_SIZE = 2.6  # shape mark size that fills a logo zone without clipping (the default draws it at ~40%)


MATCH_LOGO = 76  # logo badges on the next-game card and the scout header


def matchup_logos(y: int, logo_a: str, logo_b: str, size: int = MATCH_LOGO) -> list[Zone]:
    """Ana takım and Rakip logos side by side, right-aligned at the page edge, with a dash between."""
    x = 1184 - 2 * size - 28
    return [Zone("sheet", x, y, size, size, sheet=logo_a, show_title=False, margin=4, background=CARD),
            Zone("text", x + size + 6, y + size // 2 - 14, 18, 28, runs=[("–", T_TITLE)]),
            Zone("sheet", x + size + 28, y, size, size, sheet=logo_b, show_title=False, margin=4, background=CARD)]


CANVAS = "#eef1f4"  # the page behind the cards (60-30-10: neutral canvas, ink text, the team colour as accent)
CARD = "#ffffff"
HEADER_H = NAV_H + 66  # the team-coloured block: page bar, logo, title and subtitle
PAGE_H = 1134  # every page this tall (the scout page is longer): same scrollbar, no sideways jump between pages
T_TITLE_ON = {**T_TITLE, "fontcolor": "#ffffff"}
T_SUB_ON = {**T_SUB, "fontcolor": "#e9eef2"}


def with_nav(dash: Dashboard, band: str, logo: str, tbl_logo: str | None) -> Dashboard:
    """Page chrome: a header block in the chosen team's (dark) colour with the page bar, the team's logo,
    the title in white and the TBL logo; below it a grey canvas with every view on a white card. Pages
    shorter than PAGE_H are stretched to it, the footer to the new bottom."""
    old_h = dash.height
    dash.zones = [z for z in dash.zones if not (z.kind == "blank" and z.y in (0, 66))]  # accent bar, title rule
    for z in dash.zones:
        if z.kind == "text" and z.y in (10, 42) and z.x == 16:  # page title and subtitle, white on the header
            z.x, z.w = z.x + LOGO_W + 12, 1010 - LOGO_W - 28
            z.runs = [(t, T_TITLE_ON if st is T_TITLE else T_SUB_ON if st is T_SUB else st) for t, st in z.runs]
        z.y += NAV_H
        if z.kind in ("sheet", "legend") and z.background is None and z.margin is None:
            z.background = CARD
    height = max(old_h + NAV_H, PAGE_H)
    for z in dash.zones:  # the footer sits at the bottom of the page
        if z.kind == "text" and z.y == old_h - 22 + NAV_H:
            z.y = height - 22
    chrome = [Zone("blank", 0, 0, 1200, height, background=CANVAS),
              Zone("sheet", 0, 0, 1200, HEADER_H, sheet=band, show_title=False, margin=0)]
    badges = [Zone("sheet", 16, NAV_H + 6, LOGO_W + 4, LOGO_W + 4, sheet=logo, show_title=False, margin=2,
                   background=CARD)]
    if tbl_logo:
        badges.append(Zone("image", 1030, NAV_H + 8, 154, 50, image=tbl_logo, background=CARD, margin=4))
    x = 16
    for target in NAV:
        label = NAV_TITLE.get(target, target)
        w = 46 + 7 * len(label)  # Tableau Medium 9 pt plus the button's padding
        badges.append(Zone("nav", x, 6, w, 26, target=target, title=label))
        x += w + 4
    dash.zones = chrome + dash.zones + badges  # later zones are drawn on top
    dash.height = height
    return dash


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
    lost = gf.filter(pl.col("forfeit") & (pl.col("official_pts") == 0)).group_by("team").len().sort("team")
    forfeits = [f"{nick[t]} ({n})" for t, n in lost.iter_rows()]  # team (games it lost 0-20 at the table)
    rating_se = round(110 / (tp.filter(~pl.col("is_league"))["poss"].mean() ** 0.5))  # 1 SE of a team rating
    qmin = int(pp["qualify_min"][0])
    rel = pl.read_csv(d / "stat_reliability.csv")
    rel_from = rel["fitted_on"][0] if rel.height else season
    season_games = int(round(rel["season_games"][0])) if rel.height else g_hi
    cal = pl.read_csv(d / "xpts_calibration.csv").filter(pl.col("method") != "ideal")
    cal_used = cal.filter(pl.col("used")).row(0, named=True)
    frozen = cal.filter(pl.col("method") == "prior_frozen")
    frozen = frozen.row(0, named=True) if frozen.height else None
    bt = pl.read_csv(d / "prior_backtest.csv")
    bt_n = int(bt["with_prior"].max()) if bt.height else 0
    footer = (f"Veri: TBF ve FIBA LiveStats kamuya açık maç verisi · {n_games} maç, {weeks[0]}–{weeks[1]}. hafta · "
              f"TBL {season} {span} · tbl-vision")

    # showcase defaults, so that no view is empty when nothing is selected
    showcase_team = tp.filter(~pl.col("is_league")).sort("net", descending=True)["team"][0]
    ranked = pp.filter(pl.col("qualified")) if pp["qualified"].any() else pp  # one-game test data has no 100-minute player
    top = ranked.sort("minutes", descending=True).group_by("team", maintain_order=True).first()
    top_of = dict(top.select("team", "player").iter_rows())
    top_case = (f"CASE {GOSTER} " + " ".join(f'WHEN "{t}" THEN "{p}"' for t, p in top_of.items())
                + f' ELSE "{top_of[showcase_team]}" END')
    shown_team = GOSTER
    # Güven: the roster of the chosen team (else of the chosen player, else the showcase team); the table shows the
    # chosen player if they play for it, else its top player, so picking another team never leaves a stale player
    roster_team = GOSTER
    roster_player = (f'IF {{Oyuncu}} <> "{ALL}" AND [Calculation_oyuncunun_takimi] = [Calculation_kadro_takim] '
                     'THEN {Oyuncu} ELSE CASE [Calculation_kadro_takim] '
                     + " ".join(f'WHEN "{t}" THEN "{p}"' for t, p in top_of.items()) + " END END")

    def roster_block() -> list[Calc]:
        return [
            Calc("oyuncunun_takimi", "Oyuncunun Takımı", '{FIXED : MAX(IF [player] = {Oyuncu} THEN [team] END)}',
                 "string"),
            Calc("kadro_takim", "Kadro Takımı", roster_team, "string"),
            Calc("kadro_oyuncu", "Kadro Oyuncusu", roster_player, "string"),
        ]

    comeback = gf.filter(pl.col("won")).sort("largest_deficit", descending=True).row(0, named=True)
    matches = gf.filter(pl.col("is_home")).sort("date", "match_id")
    match_members = [(int(r["match_id"]), f"{r['week']}. hf · {r['team_short']} {r['pts']}–{r['opp_pts']} {r['opponent_short']}")
                     for r in matches.iter_rows(named=True)]

    strongest = tp.filter(~pl.col("is_league")).sort("net", descending=True)["team"].to_list()
    home_team = DEFAULT_TEAM if DEFAULT_TEAM in nick else strongest[0]
    p_team = Param("Ana takım", "string", home_team, members=[(t, nick[t]) for t in teams])
    p_opp = Param("Rakip", "string", NEXT, members=[(NEXT, None)] + [(t, nick[t]) for t in teams])
    # grouped by team, most minutes first: a team's roster sits together in the list
    players = pp.with_columns(order=pl.col("team").replace_strict({t: i for i, t in enumerate(teams)}, default=len(teams)))
    p_player = Param("Oyuncu", "string", ALL, members=[(ALL, None)] + [
        (r["player"], f"{r['team_short']} · {r['player'].split(' (')[0]}")  # a mid-season transfer's name carries the club
        for r in players.sort("order", "minutes", descending=[False, True]).iter_rows(named=True)])
    p_side = Param("Taraf", "string", "Hücum", members=[("Hücum", None), ("Savunma", None)])
    p_ctx = Param("Şut Bağlamı", "string", ALL, members=[(v, None) for v in
                                                        (ALL, "Hızlı hücum", "Set hücum", "İkinci şans",
                                                         "Top kaybı sonrası", "Clutch")])
    p_map = Param("Harita Rengi", "string", "Şut değeri (xPTS)",
                  members=[("Şut değeri (xPTS)", None), ("Şut başarısı (POE)", None)])
    p_min_shots = Param("Min. şut", "integer", 50, range=(20, 100, 10))
    p_min_poss = Param("Min. hücum (ikili)", "integer", 100, range=(50, 300, 25))
    p_match = Param("Maç", "integer", LAST, members=[(LAST, "Ana takımın son maçı")] + match_members)
    # team colours and logos (assets.py): every theme follows the chosen team
    th = pl.read_csv(d / "team_theme.csv")
    color_of = dict(th.select("team", "color").iter_rows())
    THEME["color"] = dict(th.select("team_short", "color").iter_rows())
    THEME["light"] = dict(th.select("team_short", "color_light").iter_rows())
    theme = Source("team_theme.csv", "Tema", calcs=[
        Calc("ana", "Ana takım", "[team] = {Ana takım}", "boolean"),
        Calc("rakip", "Rakip", f"[team] = {RAKIP}", "boolean"),
        Calc("bir", "Bir", "1", "integer", "measure", "quantitative")],
        colors={"[none:team:nk]": color_of},
        shapes={"[none:team:nk]": {r["team"]: f"{PALETTE}/{r['logo']}" for r in th.iter_rows(named=True)}})
    logo_a = Sheet("Logo · ana takım", theme, mark="Shape", shape=F("team"), filters=[("true", F("Calculation_ana"))],
                   mark_size=LOGO_SIZE, tooltip_runs=[("<[none:team:nk]>", TIP_B)])
    # Tableau allows a sheet once per dashboard: the next-game card has its own copy of the team's logo
    logo_a2 = Sheet("Logo · ana takım (maç)", theme, mark="Shape", shape=F("team"),
                    filters=[("true", F("Calculation_ana"))], mark_size=LOGO_SIZE,
                    tooltip_runs=[("<[none:team:nk]>", TIP_B)])
    logo_b = Sheet("Logo · rakip", theme, mark="Shape", shape=F("team"), filters=[("true", F("Calculation_rakip"))],
                   mark_size=LOGO_SIZE, tooltip_runs=[("<[none:team:nk]>", TIP_B)])
    band_src = Source("theme_band.csv", "Tema bloğu", dimensions=("x",),
                      calcs=[Calc("ana", "Ana takım", "[team] = {Ana takım}", "boolean")],
                      colors={"[none:team:nk]": dict(th.select("team", "color_dark").iter_rows())})
    band = Sheet("Tema şeridi", band_src, cols=[F("x", "none", "qk")], rows=[F("y", "sum", "qk")], mark="Area",
                 color=F("team"), filters=[("true", F("Calculation_ana"))], plain=True, opacity=255,
                 axes=[Axis(F("x", "none", "qk"), "cols", 0, 1, hidden=True, title=""),
                       Axis(F("y", "sum", "qk"), "rows", 0, 1, hidden=True, title="")],
                 tooltip_runs=[("<[none:team:nk]>", TIP_B)])
    tbl_logo = str((d / "TBL.png").resolve()) if (d / "TBL.png").exists() else None
    p_show = Param("Görünüm", "string", "Ana takım", members=[("Ana takım", None), ("Rakip", None)])
    params = [p_team, p_opp, p_show, p_player, p_side, p_ctx, p_map, p_min_shots, p_min_poss, p_match]

    # the opponent each team meets next (else the strongest other team) and each team's last game
    proj = pl.read_csv(d / "mx_projection.csv")
    nxt = dict(proj.filter(pl.col("fixture_ts").is_not_null() & (pl.col("venue") == "Fikstüre göre")).sort("fixture_ts")
               .unique("team", keep="first").select("team", "opponent").iter_rows())
    opp_of = {t: nxt.get(t) or next(x for x in strongest + teams if x != t) for t in teams}
    rakip_expr = (f'(IF {{Rakip}} = "{NEXT}" THEN (CASE {{Ana takım}} '
                  + " ".join(f'WHEN "{t}" THEN "{o}"' for t, o in opp_of.items()) + " END) ELSE {Rakip} END)")
    last = dict(gf.sort("date", "match_id").unique("team", keep="last").select("team", "match_id").iter_rows())
    mac_expr = (f"(IF {{Maç}} = {LAST} THEN (CASE {{Ana takım}} "
                + " ".join(f'WHEN "{t}" THEN {int(m)}' for t, m in last.items()) + " END) ELSE {Maç} END)")

    # -- Takımlar ----------------------------------------------------------------------------
    kpis = Source("team_kpis.csv", "Takım künyesi", dimensions=("metric_order",), calcs=[
        Calc("kpi_sira", "KPI sırası", f"IF [team] = {{Ana takım}} THEN 1 ELSEIF [team] = {RAKIP} THEN 2 ELSE 3 END",
             "integer", "measure", "quantitative"),
        Calc("kpi_satir", "KPI Satırı", f"[is_league] OR [team] = {{Ana takım}} OR [team] = {RAKIP}",
             "boolean")])
    profiles = Source("team_profiles.csv", "Takım profili", dimensions=("team_season_id",), calcs=[
        *team_block(), Calc("takimlar", "Takımlar", "NOT [is_league]", "boolean")],
        captions={"ortg": "Hücum reytingi (ORtg)", "drtg": "Savunma reytingi (DRtg)", "net": "Net reyting",
                  "pace": "Tempo", "team": "Takım", "team_short": "Takım (kısa)", "poss": "Hücum sayısı"},
        colors={"[none:Calculation_takim_vurgu:nk]": team_colors()})
    factors = Source("four_factors.csv", "Dört faktör", dimensions=("team_season_id", "factor_order"),
                     calcs=team_block(), captions={"z": "Ligden fark (z)", "value_text": "Değer",
                                                   "factor_short": "Faktör", "side": "Taraf"})
    starts = Source("possession_starts.csv", "Hücum başlangıçları", dimensions=("team_season_id", "start_order"), calcs=[
        *team_block(),
        Calc("bas_filtre", "Başlangıç Filtresi",
             f'[side] = {{Taraf}} AND [start_type] <> "inferred" AND [start_type] <> "jumpball" '
             f'AND [start_type] <> "unsportsmanlike_foul" AND ({{Ana takım}} = "{ALL}" OR [team] = {{Ana takım}})', "boolean"),
        Calc("ppp", "Hücum başına sayı", "SUM([pts]) / SUM([poss])", "real", "measure", "quantitative"),
        Calc("lig_ppp", "Lig ort.", "AVG([league_ppp])", "real", "measure", "quantitative"),
        Calc("ppp_fark", "Ligden iyi fark", 'IF {Taraf} = "Hücum" THEN [Calculation_ppp] - [Calculation_lig_ppp] '
             'ELSE [Calculation_lig_ppp] - [Calculation_ppp] END', "real", "measure", "quantitative"),
        Calc("ppp_renk", "Renk",
             f'IF {{Ana takım}} = "{ALL}" THEN "Lig" ELSEIF SUM([poss]) < 30 THEN "Az veri (<30 hücum)" '
             'ELSEIF [Calculation_ppp_fark] >= 0.05 THEN "Ligden iyi" ELSEIF [Calculation_ppp_fark] <= -0.05 '
             'THEN "Ligden kötü" ELSE "Lig düzeyinde" END', "string"),
    ], captions={"start": "Hücum başlangıcı", "poss": "Hücum sayısı", "share": "Pay"},
        colors={"Calculation_ppp_renk": {"Lig": SEC, "Az veri (<30 hücum)": LOW, "Ligden iyi": GOOD,
                                         "Ligden kötü": BAD, "Lig düzeyinde": MID}})

    t1 = Sheet("T1 Takım künyesi", kpis, rows=[F("header")], cols=[F("metric")], mark="Text",
               label=[F("value_text")], filters=[("true", F("Calculation_kpi_satir"))], tooltip=TERM_FIELDS,
               sorts=[(F("metric"), F("metric_order", "sum", "qk"), "ASC", "columns"),
                      (F("header"), F("Calculation_kpi_sira", "min", "qk"), "ASC", "rows")],
               tooltip_runs=tip(*TERM_TIP, NL, "<[none:header:nk]>", ": ", "<[none:value_text:nk]>"))
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
                                NL, "Tıklayın: bu takım bütün sayfalarda seçilir") + defs("ortg", "drtg"))
    t3 = Sheet("T3 Dört faktör", factors, rows=[F("Calculation_takim_etiket")], cols=[F("side"), F("factor_short")],
               mark="Square", color=F("z", "sum", "qk"), color_range=(-2.0, 2.0), label=[F("value_text")],
               detail=[F("Calculation_takim_tik"), F("team")], tooltip=[F("league_text"), *TERM_FIELDS],
               sorts=[(F("Calculation_takim_etiket"), F("net", "avg", "qk"), "DESC", "rows"),
                      (F("factor_short"), F("factor_order", "min", "ok"), "ASC", "columns")],
               title=title("Dört faktör", "Mavi: ligden iyi · Kırmızı: ligden kötü · Satırlar net reytinge göre"),
               tooltip_runs=tip(*TERM_TIP, NL, "<[none:team:nk]>", ": ", "<[none:value_text:nk]>", "  ·  Lig: ",
                                "<[none:league_text:nk]>"))
    t4 = Sheet("T4 Hücum başlangıçları", starts, rows=[F("start")], cols=[F("Calculation_ppp", "usr", "qk")],
               mark="Bar", color=F("Calculation_ppp_renk", "usr", "nk"), label=[F("Calculation_ppp", "usr", "qk")],
               tooltip=[F("poss", "sum", "qk"), F("Calculation_lig_ppp", "usr", "qk"), F("term_desc")],
               filters=[("true", F("Calculation_bas_filtre"))],
               sorts=[(F("start"), F("start_order", "min", "ok"), "ASC", "rows")],
               axes=[Axis(F("Calculation_ppp", "usr", "qk"), "cols", 0, 1.6, title="Hücum başına sayı", fmt=FMT_1)],
               formats={"[usr:Calculation_ppp:qk]": FMT_2, "[usr:Calculation_lig_ppp:qk]": FMT_2},
               title=title("Hücum nasıl başladı, kaç sayı getirdi?", None, [(" · ", T_SHEET), ("<Parameters.Taraf>", T_SHEET)]),
               tooltip_runs=tip("<[none:start:nk]>", NL, "<[none:term_desc:nk]>", NL, "Hücum başına sayı: ",
                                "<[usr:Calculation_ppp:qk]>", NL, "Lig: ", "<[usr:Calculation_lig_ppp:qk]>", NL, "Hücum: ",
                                "<[sum:poss:qk]>"))

    # -- Şut Kalitesi ------------------------------------------------------------------------
    ctx = {"Hızlı hücum": "[transition]", "Set hücum": "NOT [transition] AND NOT [second_chance]",
           "İkinci şans": "[second_chance]", "Top kaybı sonrası": "[from_turnover]", "Clutch": "[clutch]"}
    ctx_case = f'CASE {{Şut Bağlamı}} WHEN "{ALL}" THEN TRUE ' + " ".join(
        f'WHEN "{k}" THEN {v}' for k, v in ctx.items()) + " END"
    shots = Source("shots.csv", "Şutlar", dimensions=("match_id", "week", "plot_x", "plot_y"), calcs=[
        Calc("sut_filtre", "Şut Filtresi",
             f'(IF {{Oyuncu}} <> "{ALL}" THEN [player] = {{Oyuncu}} '
             f'ELSEIF {{Taraf}} = "Hücum" THEN [team] = {GOSTER} ELSE [opponent] = {GOSTER} END) '
             f'AND [zone] <> "heave" AND [plot_y] <= 14 AND NOT [location_imputed] AND ({ctx_case})', "boolean"),
        Calc("hexx", "Hex X", "HEXBINX([plot_x] / 0.6, [plot_y] / 0.6) * 0.6", "real", "dimension", "quantitative"),
        Calc("hexy", "Hex Y", "HEXBINY([plot_x] / 0.6, [plot_y] / 0.6) * 0.6", "real", "dimension", "quantitative"),
        Calc("n", "Şut sayısı", "COUNT([xpts])", "integer", "measure", "quantitative"),
        Calc("isabet", "İsabet %", "AVG(IIF([made], 1, 0))", "real", "measure", "quantitative"),
        Calc("sayi_sut", "Sayı/şut", "SUM([pts]) / COUNT([xpts])", "real", "measure", "quantitative"),
        Calc("xpts_sut", "xPTS/şut", "SUM([xpts]) / COUNT([xpts])", "real", "measure", "quantitative"),
        Calc("renk", "Sayı/şut farkı",
             f'(IF {{Taraf}} = "Savunma" AND {{Ana takım}} <> "{ALL}" AND {{Oyuncu}} = "{ALL}" THEN -1 ELSE 1 END) * '
             f'(IF {{Harita Rengi}} = "Şut değeri (xPTS)" THEN [Calculation_xpts_sut] - {league["xpts_per_shot"]} '
             'ELSE (SUM([pts]) - SUM([xpts])) / (COUNT([xpts]) + 5) END)', "real", "measure", "quantitative"),
        Calc("goster", "Göster",
             f'IIF([Calculation_n] >= (IF {{Oyuncu}} <> "{ALL}" THEN 1 ELSEIF {{Ana takım}} <> "{ALL}" THEN 3 ELSE 8 END), 1, 0)',
             "integer", "measure", "quantitative"),
        Calc("baslik", "Başlık Seçim",
             f'(IF {{Oyuncu}} <> "{ALL}" THEN {{Oyuncu}} ELSE {GOSTER} + '
             '(IF {Taraf} = "Hücum" THEN " · attığı şutlar" ELSE " · rakiplerinin şutları" END) '
             f'END) + (IF {{Şut Bağlamı}} <> "{ALL}" THEN " · " + {{Şut Bağlamı}} ELSE "" END)',
             "string"),
    ], image=("court.png", "Calculation_hexx", "Calculation_hexy", (0.0, 15.0, 0.0, 14.0)))
    pshoot = Source("player_shooting.csv", "Oyuncu şut", dimensions=("team_season_id", "player_id"), calcs=[
        *team_block(short=False), *player_block(top_case),
        Calc("min_sut", "Min Şut Filtresi", "[shots] >= {Min. şut}", "boolean"),
    ], captions={"xpts_per_shot": "Şut kalitesi (xPTS/şut)", "poe_per_shot": "Şut başarısı (POE/şut)",
                 "shots": "Şut", "player": "Oyuncu", "team": "Takım"},
        colors={"[none:Calculation_oyuncu_vurgu:nk]": player_colors()})
    tshoot = Source("team_shooting.csv", "Takım şut", dimensions=("team_season_id",), calcs=[
        *team_block(),
        Calc("hx", "Aldığı şutların kalitesi", 'SUM(IF [side] = "offense" THEN [xpts_per_shot] END)', "real", "measure",
             "quantitative"),
        Calc("sx", "Rakibe verdiği şutların kalitesi", 'SUM(IF [side] = "defense" THEN [xpts_per_shot] END)', "real",
             "measure", "quantitative"),
        Calc("hp", "Hücum POE/şut", 'SUM(IF [side] = "offense" THEN [poe_per_shot] END)', "real", "measure",
             "quantitative"),
        Calc("ts_etiket", "Etiket", f'IF {{Ana takım}} = "{ALL}" OR [team] = {{Ana takım}} THEN [team_short] END', "string"),
        Calc("sp", "Rakip POE/şut", 'SUM(IF [side] = "defense" THEN [poe_per_shot] END)', "real", "measure",
             "quantitative"),
    ], colors={"[none:Calculation_takim_vurgu:nk]": team_colors()})

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
                                "<[usr:Calculation_xpts_sut:qk]>") + defs("xpts_per_shot", "poe_per_shot"))
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
                                NL, "2 SE aralığı: ", "<[none:interval_text:nk]>", NL, "<[none:noise:nk]>")
               + defs("xpts_per_shot", "poe_per_shot"))
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
                                "<[usr:Calculation_sx:qk]>", " xPTS, rakip sokma ", "<[usr:Calculation_sp:qk]>")
               + defs("xps", "o_xps"))

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
             # a fifth of the qualifying minutes (50 for a short season): fewer is noise and crowds the list
             f"[team] = [Calculation_gosterilen_takim] AND [minutes] >= {max(50, qmin // 5)} AND NOT ISNULL([on_off])",
             "boolean"),
        Calc("onoff_renk", "OnOff Renk", f'IF [minutes] < {qmin} THEN "Az veri" ELSEIF [on_off] >= 0 '
                                         'THEN "Sahadayken daha iyi" ELSE "Sahadayken daha kötü" END', "string"),
        Calc("onoff_satir", "Oyuncu", 'IF [player] = {Oyuncu} THEN "▶ " + [Calculation_kisa_ad] '
                                      'ELSE [Calculation_kisa_ad] END', "string"),
        *roster_block()[1:],
        Calc("kadro_filtre", "Kadro Filtresi", "[team] = [Calculation_kadro_takim] AND [minutes] > 0", "boolean"),
        Calc("kadro_satir", "Kadro", 'IF [player] = [Calculation_kadro_oyuncu] THEN "▶ " + [Calculation_kisa_ad] '
                                     'ELSE [Calculation_kisa_ad] END', "string"),
        Calc("kadro_renk", "Kadro Renk", f'IF [player] = [Calculation_kadro_oyuncu] THEN {tag("Seçili oyuncu")} '
                                         f'ELSE {tag("Seçili takım")} END', "string"),
        Calc("kadro_kisa", "Kadro Takımı (kısa)", short_case.replace("gosterilen_takim", "kadro_takim"), "string"),
        Calc("serit_filtre", "Şerit filtresi", f"[team] = {GOSTER} AND [minutes] >= 30", "boolean"),
    ], captions={"usg": "Kullanım (USG%)", "ts": "TS%", "on_off": "On/off farkı", "minutes": "Dakika",
                 "player": "Oyuncu", "team": "Takım"},
        colors={"[none:Calculation_oyuncu_vurgu:nk]": player_colors(), "[none:Calculation_kadro_renk:nk]": player_colors(),
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
               detail=[F("player", "attr", "nk"), F("team", "attr", "nk"), F("sub", "attr", "nk")], tooltip=TERM_FIELDS,
               tooltip_runs=tip(*TERM_TIP, NL, "Değer: ", "<[none:value_text:nk]>"),
               filters=[("true", F("Calculation_profil_filtre"))],
               sorts=[(F("metric"), F("metric_order", "sum", "qk"), "ASC", "columns")],
               title=[("<[attr:player:nk]>", {"fontname": "Tableau Semibold", "fontsize": "16", "fontcolor": INK}),
                      ("  ·  ", T_SHEET2), ("<[attr:team:nk]>", T_SHEET2), NL, ("<[attr:sub:nk]>", T_SHEET2)])
    o2 = Sheet("O2 Yüzdelik profil", pct, rows=[F("group"), F("label")], cols=[F("pct", "avg", "qk")], mark="Bar",
               color=F("pct_class"), label=[F("value_text")], filters=[("true", F("Calculation_profil_filtre"))],
               tooltip=TERM_FIELDS,
               sorts=[(F("group"), F("metric_order", "min", "ok"), "ASC", "rows"),
                      (F("label"), F("metric_order", "min", "ok"), "ASC", "rows")],
               axes=[Axis(F("pct", "avg", "qk"), "cols", 0, 100, title="Yüzdelik (50 = lig ortancası)", fmt=FMT_0)],
               reflines=[RefLine(F("pct", "avg", "qk"), 50.0)], formats={"[avg:pct:qk]": FMT_0},
               title=title("Ligdeki yeri", f"{qmin}+ dakika oynayanlar arasında yüzdelik · gri çubuklar oyun stilidir"),
               tooltip_runs=tip(*TERM_TIP, NL, "Değer: ", "<[none:value_text:nk]>", "  ·  yüzdelik: ", "<[avg:pct:qk]>"))
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
                                "  ·  TS% ", "<[avg:ts:qk]>", NL, "<[sum:minutes:qk]>", " dakika") + defs("usg", "ts"))
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
                                "<[sum:minutes:qk]>", " dakika") + defs("on_off"))

    k1 = Sheet("K Oyuncu seçimi", pprof, cols=[F("Calculation_serit_ad")], mark="Text", label=[F("mpg", "avg", "qk")],
               color=F("Calculation_oyuncu_vurgu"), detail=[F("player"), F("Calculation_oyuncu_tik")],
               filters=[("true", F("Calculation_serit_filtre"))],
               sorts=[(F("Calculation_serit_ad"), F("minutes", "sum", "qk"), "DESC", "columns")],
               formats={"[avg:mpg:qk]": 'n#,##0.0" dk";-#,##0.0" dk"'},
               tooltip_runs=tip("<[none:player:nk]>", NL, "<[avg:mpg:qk]>", " / maç", NL,
                                "Tıklayın: oyuncu seçilir · tekrar tıklayın: seçim kalkar"))

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
                                "<[none:sample:nk]>") + defs("net"))
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
                      calcs=[Calc("mac_filtre", "Maç Filtresi", f"[match_id] = {MAC}", "boolean")],
                      captions={"minute": "Dakika", "margin_home": "Skor farkı"})
    summary = Source("match_summary.csv", "Maç özeti", dimensions=("match_id", "metric_order"),
                     calcs=[Calc("mac_filtre", "Maç Filtresi", f"[match_id] = {MAC}", "boolean")])
    flow = Source("game_flow.csv", "Maç akışı", dimensions=("match_id", "team_season_id", "opponent_id", "week"), calcs=[
        *team_block(),
        Calc("geri_donus", "Geri Dönüş Filtresi",
             f"[won] AND [largest_deficit] >= 8 AND ([comeback_rank] <= {TOP_COMEBACKS} OR [team] = {{Ana takım}})", "boolean"),
        Calc("mac_vurgu", "Maç Vurgu",
             f'IF [match_id] = {MAC} THEN "Seçili maç" ELSEIF {{Ana takım}} <> "{ALL}" AND ([team] = {{Ana takım}} '
             'OR [opponent] = {Ana takım}) THEN "Seçili takımın maçı" ELSE "Diğer" END', "string"),
    ], captions={"largest_deficit": "En çok geride", "comeback": "Maç"},
        colors={"[none:Calculation_mac_vurgu:nk]": {"Seçili maç": ACCENT, "Seçili takımın maçı": SEC, "Diğer": GREY}})
    runs = Source("runs.csv", "Seriler", dimensions=("match_id", "team_season_id", "start_period"),
                  calcs=[Calc("mac_filtre", "Maç Filtresi", f"[match_id] = {MAC}", "boolean")])
    clutch = Source("clutch_team.csv", "Clutch takım", dimensions=("team_season_id",), calcs=[
        *team_block(),
        Calc("clutch_renk", "Clutch Renk", f'IF [team] = {{Ana takım}} THEN {tag("Seçili takım")} ELSEIF [poss] < 20 '
                                           f'THEN "Az veri (<20 hücum)" ELSEIF {{Ana takım}} = "{ALL}" THEN "{ALL}" '
                                           'ELSE "Diğer" END', "string"),
    ], captions={"net": "Clutch net reyting"},
        colors={"[none:Calculation_clutch_renk:nk]": {**tinted("Seçili takım"), "Az veri (<20 hücum)": LOW, ALL: SEC,
                                                       "Diğer": GREY}})
    m1 = Sheet("M1 Skor farkı", timeline, cols=[F("minute", "none", "qk")], rows=[F("margin_home", "none", "qk")],
               mark="Line", aggregate=False, line_step=True, tooltip=[F("clock_label"), F("score_label"), F("player")],
               detail=[F("match", "attr", "nk"), F("date", "attr", "ok")],
               filters=[("true", F("Calculation_mac_filtre"))],
               axes=[Axis(F("minute", "none", "qk"), "cols", title="Dakika", fmt=FMT_0),
                     Axis(F("margin_home", "none", "qk"), "rows", title="Fark (ev sahibi − deplasman)", fmt=FMT_0)],
               reflines=[RefLine(F("margin_home", "none", "qk"), 0.0), RefLine(F("minute", "none", "qk"), 10.0, "Ç2"),
                         RefLine(F("minute", "none", "qk"), 20.0, "Ç3"), RefLine(F("minute", "none", "qk"), 30.0, "Ç4")],
               title=title("", "↑ ev sahibi önde · ↓ deplasman önde",
                           # one field: the line's marks are disaggregated, so MAX(score) would read "0 to 91"
                           [("<[attr:match:nk]>", T_SHEET), (" · ", T_SHEET2), ("<[attr:date:ok]>", T_SHEET2)]),
               tooltip_runs=tip("<[none:clock_label:nk]>", NL, "<[none:player:nk]>", NL, "Skor ",
                                "<[none:score_label:nk]>"))
    m2 = Sheet("M2 Maç özeti", summary, rows=[F("team"), F("result")], cols=[F("metric")], mark="Text",
               label=[F("value_text")], filters=[("true", F("Calculation_mac_filtre"))], tooltip=TERM_FIELDS,
               tooltip_runs=tip(*TERM_TIP, NL, "<[none:team:nk]>", ": ", "<[none:value_text:nk]>"),
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
               title=title("En büyük geri dönüşler", f"İlk {TOP_COMEBACKS} ve seçili takımınkiler · tıklayın: maç akışı sağda açılır"),
               tooltip_runs=tip("<[none:comeback:nk]>", NL, "En çok ", "<[sum:largest_deficit:qk]>", " sayı gerideydi")
               + defs("ms_deficit"))
    m5 = Sheet("M5 Clutch", clutch, rows=[F("Calculation_takim_etiket")], cols=[F("net", "sum", "qk")], mark="Bar",
               color=F("Calculation_clutch_renk"), label=[F("label")], detail=[F("team"), F("Calculation_takim_tik")],
               sorts=[(F("Calculation_takim_etiket"), F("net", "sum", "qk"), "DESC", "rows")],
               axes=[Axis(F("net", "sum", "qk"), "cols", title="Clutch net reyting", fmt=FMT_0)],
               reflines=[RefLine(F("net", "sum", "qk"), 0.0)],
               title=title("Clutch: son 5 dakika, fark ≤ 5", "Açık gri: 20 hücumdan az · sıralama şansa açıktır"),
               tooltip_runs=tip("<[none:team:nk]>", NL, "<[none:label:nk]>") + defs("clutch"))

    # -- Güven -------------------------------------------------------------------------------
    settle = Source("stat_reliability.csv", "Oturma noktası", dimensions=("metric_order",),
                    captions={"k_games": "Maç", "label": "İstatistik"},
                    colors={"[none:settles:nk]": {"Çeyrek sezonda": GOOD, "Bir sezon içinde": GOOD_L,
                                                  "Bir sezon yetmez": GREY}})
    calib = Source("xpts_calibration.csv", "xPTS kalibrasyonu", dimensions=("bin",),
                   captions={"xfg": "Beklenen isabet (xFG)", "fg": "Gerçek isabet", "gap": "Gerçek − beklenen"},
                   colors={"[none:method_label:nk]": {"Geçen + bu sezon": INK, "Kullanılan: yalnız bu sezon (5 katlı)": INK,
                                                      "Geçen sezonun modeli (dondurulmuş)": GOOD,
                                                      "Yalnız bu sezon (5 katlı)": GREY}})
    btest = Source("prior_backtest.csv", "Geçen sezon testi", dimensions=("metric_order", "method_order"),
                   captions={"value": "Tahmin hatası"},
                   colors={"[none:method:nk]": {"Ham değer": GREY, "Lig ortalamasıyla": GOOD_L, "+ geçen sezon": GOOD}})
    stable = Source("player_stable.csv", "Dengeli değerler", dimensions=("metric_order", "col_order"), calcs=[
        *roster_block(), Calc("profil_filtre", "Profil Filtresi", "[player] = [Calculation_kadro_oyuncu]", "boolean")])

    g0 = Sheet("G0 Kadro", pprof, rows=[F("Calculation_kadro_satir")], cols=[F("minutes", "sum", "qk")], mark="Bar",
               color=F("Calculation_kadro_renk"), label=[F("minutes", "sum", "qk")],
               detail=[F("player"), F("team"), F("Calculation_oyuncu_tik"), F("Calculation_kadro_kisa", "attr", "nk")],
               tooltip=[F("games", "sum", "qk")], filters=[("true", F("Calculation_kadro_filtre"))],
               sorts=[(F("Calculation_kadro_satir"), F("minutes", "sum", "qk"), "DESC", "rows")],
               axes=[Axis(F("minutes", "sum", "qk"), "cols", title="Dakika", fmt=FMT_0)],
               formats={"[sum:minutes:qk]": FMT_0},
               title=title("Kadro · ", "Bir oyuncuya tıklayın", [("<[attr:Calculation_kadro_kisa:nk]>", T_SHEET)]),
               tooltip_runs=tip("<[none:player:nk]>", NL, "<[sum:games:qk]>", " maç · ", "<[sum:minutes:qk]>",
                                " dakika"))

    g1 = Sheet("G1 Dengeli değerler", stable, rows=[F("label")], cols=[F("col")], mark="Text", label=[F("value_text")],
               detail=[F("player", "attr", "nk"), F("team", "attr", "nk")],
               tooltip=[F("units_text"), F("weights_text"), F("term_desc"), F("col_desc")],
               filters=[("true", F("Calculation_profil_filtre"))],
               sorts=[(F("label"), F("metric_order", "min", "ok"), "ASC", "rows"),
                      (F("col"), F("col_order", "min", "ok"), "ASC", "columns")],
               title=[("<[attr:player:nk]>", {"fontname": "Tableau Semibold", "fontsize": "14", "fontcolor": INK}),
                      ("  ·  ", T_SHEET2), ("<[attr:team:nk]>", T_SHEET2), NL,
                      ("Deneme: şut, hücum ya da fırsat sayısı · * geçen sezon başka bir ligde · "
                       "ağırlıklar ipucunda", T_SHEET2)],
               tooltip_runs=tip("<[none:label:nk]>", NL, "<[none:term_desc:nk]>", NL, "<[none:col:nk]>", ": ",
                                "<[none:col_desc:nk]>", NL, "<[none:units_text:nk]>", NL, "Dengeli tahminde ağırlık: ",
                                "<[none:weights_text:nk]>"))
    g2 = Sheet("G2 Kaç maçta oturur", settle, rows=[F("label")], cols=[F("k_games", "sum", "qk")], mark="Bar",
               color=F("settles"), label=[F("k_text")], label_cull=False,
               tooltip=[F("interval_text"), F("season_text"), F("term_desc")],
               sorts=[(F("label"), F("k_games", "sum", "qk"), "ASC", "rows")],
               axes=[Axis(F("k_games", "sum", "qk"), "cols", title="Maç (~25 dk/maç oynayan için)", fmt=FMT_0)],
               reflines=[RefLine(F("k_games", "sum", "qk"), float(season_games))],
               title=title("Hangi istatistik kaç maçta oturur?",
                           f"Bu noktada değerin yarısı beceri, yarısı şans · çizgi: bir sezon ({season_games} maç) · "
                           f"koyu: çeyrek sezonda, açık: bir sezonda, gri: bir sezon yetmez · {rel_from}"),
               tooltip_runs=tip("<[none:label:nk]>", NL, "<[none:term_desc:nk]>", NL, "<[none:k_text:nk]>", NL, "%90 aralık: ",
                                "<[none:interval_text:nk]>", NL, "Sezon sonunda tipik oyuncuda beceri payı: ",
                                "<[none:season_text:nk]>"))
    slope_txt = (f"Geçen sezonun modeli bu sezonda: eğim {num(frozen['calibration_slope'], 2)}, sabit "
                 f"{num(frozen['calibration_intercept'], 2, True)} (ideal 1 ve 0)" if frozen else
                 f"Eğim {num(cal_used['calibration_slope'], 2)}, sabit {num(cal_used['calibration_intercept'], 2, True)} "
                 "(ideal 1 ve 0)")
    per_bin = int(cal.filter(pl.col("used"))["shots"].median())
    noise = max(1, round(2 * 100 * (0.25 / per_bin) ** 0.5))  # 2 SE of a ~50% FG% over that many shots
    g3 = Sheet("G3 xPTS kalibrasyonu", calib, cols=[F("xfg", "avg", "qk")], rows=[F("gap", "avg", "qk")], mark="Line",
               color=F("method_label"), path=F("bin", "none", "ok"), detail=[F("bin", "none", "ok")],
               tooltip=[F("shots", "sum", "qk"), F("fg", "avg", "qk")],
               axes=[Axis(F("xfg", "avg", "qk"), "cols", 0.2, 0.8, title="Beklenen isabet (xFG)", fmt=FMT_P0),
                     Axis(F("gap", "avg", "qk"), "rows", -8, 8, title="Fark (puan)", fmt=FMT_0)],
               reflines=[RefLine(F("gap", "avg", "qk"), 0.0)],
               formats={"[avg:xfg:qk]": FMT_P1, "[avg:fg:qk]": FMT_P1, "[avg:gap:qk]": "*+#,##0.0;-#,##0.0"},
               title=title("xPTS tutuyor mu?", slope_txt + f" · nokta başına ~{per_bin} şut, ±{noise} puan şans"),
               tooltip_runs=tip("<[none:method_label:nk]>", NL, "Beklenen ", "<[avg:xfg:qk]>", " · gerçek ",
                                "<[avg:fg:qk]>", NL, "<[sum:shots:qk]>", " şut"))
    g4 = Sheet("G4 Geçen sezon testi", btest, rows=[F("label")], cols=[F("value", "avg", "qk")], mark="Circle",
               color=F("method"), detail=[F("method_order", "none", "ok")], tooltip=[F("value_text"), F("term_desc")],
               sorts=[(F("label"), F("metric_order", "min", "ok"), "ASC", "rows")],
               axes=[Axis(F("value", "avg", "qk"), "cols", 0, 110, title="Tahmin hatası (ham değer = 100)", fmt=FMT_0)],
               reflines=[RefLine(F("value", "avg", "qk"), 100.0)], formats={"[avg:value:qk]": FMT_0}, mark_size=1.2,
               title=title("Geçen sezon işe yarıyor mu?",
                           f"İlk 4 maçtan sezonun kalanını tahmin, {bt_n} oyuncu · gri: ham, açık: lig ortalamasıyla, "
                           "koyu: + geçen sezon"),
               tooltip_runs=tip("<[none:label:nk]>", NL, "<[none:term_desc:nk]>", NL, "<[none:method:nk]>", ": ",
                                "<[avg:value:qk]>"))

    rk_params, rk_sources, rk_sheets, rk_dash = rakip_page(d, pprof, footer, p_team, p_opp)
    params += rk_params
    hm_sources, hm_sheets, hm_dash = home_page(d, rk_sources, p_team, footer,
                                               f"TBL {season} · {n_teams} takım · {n_games} maç ({weeks[0]}–{weeks[1]}. hafta)")
    sheets = [t1, t2, t3, t4, s1, s2, s3, o1, o2, o3, o4, i1, i2, m1, m2, m3, m4, m5, g0, g1, g2, g3, g4, k1, *rk_sheets,
              *hm_sheets, logo_a, logo_a2, logo_b, band]
    sources = [kpis, profiles, factors, starts, shots, pshoot, tshoot, pcard, pprof, pct, pairs, assists,
               timeline, summary, flow, runs, clutch, stable, settle, calib, btest, *rk_sources, *hm_sources, theme,
               band_src]
    for src in sources:  # the opponent and the game each page shows follow the chosen team
        for c in src.calcs:
            c.formula = c.formula.replace(RAKIP, rakip_expr).replace(MAC, mac_expr)

    # -- dashboards --------------------------------------------------------------------------
    def controls(*ps, widths, x=16, y=72):
        out, cx = [], x
        for p, w in zip(ps, widths):
            if p is p_team:  # chosen once on Anasayfa
                out.append(Zone("text", cx, y + 4, w, 36, runs=team_line()))
            else:
                out.append(Zone("param", cx, y, w, 42, param=p, mode="slider" if p.range else "compact"))
            cx += w + 12
        return out

    sub = f"TBL {season} · {n_teams} takım · {n_games} maç ({weeks[0]}–{weeks[1]}. hafta)"
    dashboards = [
        hm_dash,
        rk_dash,
        Dashboard("Takımlar", frame(
            "Kim hücumda, kim savunmada iyi?", sub + " · Reytingler 100 hücum başına sayıdır",
            "Sağa gidildikçe hücum, yukarı gidildikçe savunma daha iyi. Ana takım kendi renginde, rakip koyu. Bir takıma "
            f"tıklayın: rakip olarak seçilir (Rakip Analizi de ona geçer); tekrar tıklayınca sıradaki rakibe döner. "
            f"{games_txt} maçta reytinglerde ±{rating_se} puan şans payı vardır.", footer)
            + controls(p_team, p_opp, p_side, widths=[300, 240, 160]) + [
            Zone("sheet", 16, 148, 760, 84, sheet=t1.name, show_title=False),  # ana takım, rakip, lig
            Zone("sheet", 16, 238, 540, 536, sheet=t2.name),
            Zone("sheet", 568, 238, 616, 366, sheet=t3.name),
            Zone("sheet", 568, 610, 616, 164, sheet=t4.name)]),
        Dashboard("Şut Kalitesi", frame(
            "İyi şut mu alıyorlar, iyi mi atıyorlar?",
            "xPTS: şutun yerine ve bağlamına göre beklenen sayı · POE: atılan − beklenen sayı · haritada mavi iyi, "
            "kırmızı kötü · bir oyuncuya tıklayın",
            None, footer)
            + controls(p_team, p_opp, p_show, p_side, p_ctx, p_map, widths=[180, 200, 180, 140, 190, 210]) + [
            Zone("text", 16, 126, 70, 26, runs=[("Oyuncu", T_NOTE_B)]),
            Zone("sheet", 86, 112, 1098, STRIP_H, sheet=k1.name, show_title=False, fit="fit-width"),

            Zone("sheet", 16, 148, 540, 590, sheet=s1.name, map_controls=False),
            Zone("legend", 16, 740, 300, 36, sheet=s1.name, legend_field=F("Calculation_renk", "usr", "qk")),
            Zone("sheet", 568, 148, 616, 330, sheet=s2.name),
            Zone("sheet", 568, 484, 616, 290, sheet=s3.name)]),
        Dashboard("Oyuncular", frame(
            "Oyuncu ligde nerede duruyor?", f"Yüzdelikler {qmin}+ dakika oynayan oyuncular arasında · 0 en alt, 100 en üst"
            " · üstteki şeritten oyuncu seçin · on/off tek oyuncu için çok gürültülü",
            None, footer)
            + controls(p_team, p_opp, p_show, widths=[240, 220, 180]) + [
            Zone("text", 16, 126, 70, 26, runs=[("Oyuncu", T_NOTE_B)]),
            Zone("sheet", 86, 112, 1098, STRIP_H, sheet=k1.name, show_title=False, fit="fit-width"),

            Zone("sheet", 16, 148, 540, 124, sheet=o1.name),  # name, line, six values: needs ~110 px
            Zone("sheet", 16, 276, 540, 498, sheet=o2.name),
            Zone("sheet", 568, 148, 616, 330, sheet=o3.name),
            Zone("sheet", 568, 484, 616, 290, sheet=o4.name)]),
        Dashboard("İkililer", frame(
            "Hangi ikililer birlikte daha iyi?", "Aynı anda sahadayken net reyting ve biri kenardayken · asist ağı · "
            "siyah grinin sağındaysa birlikte daha iyi · ±15 şans payı",
            None, footer)
            + controls(p_team, p_opp, p_show, p_min_poss, widths=[240, 220, 180, 200]) + [
            Zone("text", 16, 126, 70, 26, runs=[("Oyuncu", T_NOTE_B)]),
            Zone("sheet", 86, 112, 1098, STRIP_H, sheet=k1.name, show_title=False, fit="fit-width"),

            Zone("sheet", 16, 148, 600, 626, sheet=i1.name, fit="fit-width"),
            Zone("sheet", 632, 148, 552, 626, sheet=i2.name)]),
        Dashboard("Maçlar", frame(
            "Maç nasıl döndü?", "Skor farkı dakika dakika · 8+ sayılık seriler · geri dönüşler · son 5 dakika (clutch)",
            "Çizgi 0'ın üstündeyse ev sahibi önde. Soldaki listeden bir maça tıklayın ya da Maç listesinden seçin. "
            "Clutch'ta takım başına çok az hücum vardır; yönü okuyun.", footer)
            + controls(p_team, p_match, widths=[300, 380]) + [
            Zone("sheet", 16, 148, 360, 250, sheet=m4.name),
            Zone("sheet", 16, 404, 360, 370, sheet=m5.name),
            Zone("sheet", 392, 148, 792, 330, sheet=m1.name),
            Zone("sheet", 392, 484, 792, 100, sheet=m2.name, show_title=False),
            Zone("sheet", 392, 590, 792, 184, sheet=m3.name)]),
        Dashboard("Güven", frame(
            "Sayılara ne kadar güvenebiliriz?",
            "Az maçla oranlar şansla oynar · dengeli tahmin bu sezonu geçen sezonla ve lig ortalamasıyla harmanlar",
            "Kadrodan oyuncuya tıklayın. Tablo ham ve dengeli değerleri, %90 aralık gerçek düzeyin büyük "
            "olasılıkla nerede olduğunu gösterir. Altta: yöntemin sınaması.",
            footer, height=GUVEN_H)
            + controls(p_team, p_opp, p_show, widths=[240, 220, 180]) + [
            Zone("sheet", 16, 148, 200, 626, sheet=g0.name),
            Zone("sheet", 228, 148, 560, 626, sheet=g1.name, fit="fit-width"),
            Zone("sheet", 800, 148, 384, 626, sheet=g2.name),
            Zone("sheet", 16, 786, 578, 290, sheet=g3.name),
            Zone("sheet", 606, 786, 578, 290, sheet=g4.name)], height=GUVEN_H),
        Dashboard("Yöntem", frame("Veri, yöntem ve sözlük", "Bu çalışma nasıl yapıldı, sayılar nasıl okunmalı", None,
                                  footer) + [
            Zone("text", 16, 80, 370, 690, runs=method_veri(n_games, weeks, season, n_teams, span, forfeits)),
            Zone("text", 410, 80, 370, 690, runs=method_sozluk()),
            Zone("text", 804, 80, 380, 690, runs=method_orneklem(games_txt, rating_se, cal_used, frozen, rel_from))]),
    ]
    actions = []

    def click(dash: str, sheet: Sheet, field: str, param: Param, caption: str) -> None:
        actions.append(ParamAction(f"{dash} · {sheet.name} · {caption}", dash, sheet.name, F(field), param))

    for sh in (t2, t3):
        click("Takımlar", sh, "Calculation_takim_tik", p_opp, "rakip")
        click("Takımlar", sh, "Calculation_sabit_tumu", p_player, "oyuncuyu sıfırla")
    click("Şut Kalitesi", s3, "Calculation_takim_tik", p_opp, "rakip")
    click("Şut Kalitesi", s3, "Calculation_sabit_tumu", p_player, "oyuncuyu sıfırla")
    for dash, sh in (("Şut Kalitesi", s2), ("Oyuncular", o3), ("Oyuncular", o4)):
        click(dash, sh, "Calculation_oyuncu_tik", p_player, "oyuncu")
    click("İkililer", i2, "Calculation_pas_tik", p_player, "pası veren")
    for dash in ("Şut Kalitesi", "Oyuncular", "İkililer"):
        click(dash, k1, "Calculation_oyuncu_tik", p_player, "kadro şeridi")
    click("Güven", g0, "Calculation_oyuncu_tik", p_player, "oyuncu")
    click("Maçlar", m4, "match_id", p_match, "maç")
    click("Maçlar", m5, "Calculation_takim_tik", p_opp, "rakip")
    # Rakip Analizi: a player opens their profile on Oyuncular
    for name in ("R7 Oyuncular", "R8 Kullanım × TS (kadro)", "R9 Rotasyon", "R0 Kimi durdurmalıyız"):
        sh = next(x for x in rk_sheets if x.name == name)
        click("Rakip Analizi", sh, "player", p_player, "oyuncu")
        actions.append(GoTo(f"Rakip Analizi · {name} · oyuncu profili", "Rakip Analizi", name, "Oyuncular"))
    click("Anasayfa", hm_sheets[4], "player", p_player, "oyuncu")
    actions.append(GoTo("Anasayfa · A5 Kadro · oyuncu profili", "Anasayfa", hm_sheets[4].name, "Oyuncular"))
    for x in dashboards:  # pages with the roster strip: the views move down to make room for it
        if any(z.sheet == k1.name for z in x.zones):
            for z in x.zones:
                if z.kind in ("sheet", "legend") and z.y >= 148:
                    z.y, z.h = z.y + STRIP_EXTRA, z.h - STRIP_EXTRA
    dashboards = [with_nav(x, band.name, logo_a.name, tbl_logo) for x in dashboards]
    shape_files = {f"{PALETTE}/{f.name}": f for f in sorted((d / "logos").glob("*.png"))} if (d / "logos").exists() else {}
    return Workbook(d, params, sources, sheets, dashboards, actions, start="Anasayfa", shape_files=shape_files)


def method_veri(n_games: int, weeks, season: str, n_teams: int, span: str, forfeits: list[str]) -> list[tuple[str, dict]]:
    forfeit_txt = (f" Oynanıp sonradan hükmen 0–20 tescil edilen maçlar: {', '.join(forfeits)}; bu maçlarda "
                   "sahada oynanan skor kullanıldı." if forfeits else "")
    return [("Veri", T_H), NL, NL, (
        f"TBL (Türkiye Basketbol Ligi) {season} {span}, {weeks[0]}–{weeks[1]}. hafta, {n_games} maç, {n_teams} takım. "
        "Kaynak: Türkiye Basketbol Federasyonu ve FIBA LiveStats'in kamuya açık maç verisi (oyun oyun kayıt, şut "
        "konumları, box score).", T_BODY), NL, NL, (
        "Hücumlar her olaydan tek tek sayıldı (Dean Oliver tanımı: hücum ribaundu hücumu uzatır). Reytingler 100 "
        "hücum başına sayıdır. Oyuncu oranları oyuncu sahadayken takımın yaptıklarına göre hesaplandı.", T_BODY), NL, NL, (
        "Veri resmi box score ile ve FIBA LiveStats'in kendi maç özetleriyle (en büyük fark, seri, liderlik değişimi, "
        "önde geçen süre) birebir doğrulandı; eşitlik sayısında LiveStats çeyrek arasındaki beraberliği her zaman "
        "saymıyor. Konumu tahmin edilen tip-in'ler ve yarı sahanın ötesinden atılan şutlar haritada gösterilmez."
        + forfeit_txt, T_BODY)]


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


def method_orneklem(games_txt: str, rating_se: int, cal: dict, frozen: dict | None, rel_from: str
                    ) -> list[tuple[str, dict]]:
    model = ("Her şut, kendi maçı dışarıda tutularak geçen sezonun ve bu sezonun öteki maçlarının şutlarıyla "
             "eğitilen modelden gelir." if cal["method"] == "pooled" else
             "Her şut, kendi maçı dışarıda tutularak eğitilen modelden gelir (maç bazında 5 katlı çapraz doğrulama).")
    test = (f" Geçen sezonun modeli hiç değiştirilmeden bu sezona uygulandığında log loss {num(frozen['log_loss'], 3)}, "
            f"kalibrasyon eğimi {num(frozen['calibration_slope'], 2)}: model sezondan sezona kaymıyor." if frozen else "")
    return [("Örneklem uyarısı", T_H), NL, NL, (
        f"Takım başına {games_txt} maç oynandı. Bir takımın reytingi ±{rating_se}, 100 hücumluk bir ikilinin net "
        "reytingi ±15 kadar şansla oynayabilir (1 standart hata). Clutch'ta takım başına az hücum vardır.", T_BODY), NL, NL, (
        "Bu yüzden oranların yanında hücum, şut ya da dakika sayısı yazar; ipuçlarında şans payı verilir. Şut "
        "haritasında az şutlu altıgenler sıfıra çekilir.", T_BODY), NL, NL, (
        f"Dengeli tahmin (Güven sayfası): her istatistiğin kaç denemede oturduğu {rel_from} sezonundan, tek ve çift "
        "maçların karşılaştırmasıyla ölçüldü. Oyuncunun oranı o kadar lig ortalamasıyla ve geçen sezonuyla "
        "harmanlanır; geçen sezonun ağırlığı, sezonun ilk maçlarından kalanını tahmin eden bir testle seçildi.",
        T_BODY), NL, NL, (
        f"xPTS modeli: lojistik regresyon. {model} Log loss {num(cal['log_loss'], 3)} (sabit isabet oranıyla "
        f"{num(cal['log_loss_constant'], 3)}); toplam beklenti atılan sayıya "
        f"%{num(abs(cal['xpts_vs_pts'] - 1) * 100, 1)} yakın.{test}", T_BODY)]


# -- Rakip Analizi ----------------------------------------------------------------------------
RAKIP_H = 3174
T_SEC = {"fontname": "Tableau Semibold", "fontsize": "15", "fontcolor": INK}  # numbered section of the scout page


def section(n: int, heading: str, sub: str, y: int) -> Zone:
    """'2 · Maçın anahtarları' with one line on what to take from it: the scout page reads top to bottom."""
    return Zone("text", 16, y, 1168, 44, runs=[(f"{n} · {heading}", T_SEC), NL, (sub, T_NOTE)])
TEAM_A, TEAM_B = "Ana takım", "Rakip"  # colours stay with the team on every view: A orange, B ink
def series_colors() -> dict[str, str]:
    return {**tinted("Ana takım hücumu"), **tinted("Ana takımın izin verdiği", light=True), **SERIES_COLORS}


SERIES_COLORS = {"Rakip hücumu": INK,
                 "Rakibin izin verdiği": SEC, "Lig": LOW}


def team_line() -> list[tuple[str, dict]]:
    """'Ana takım  OGM ORMANSPOR': the team every page is built around (chosen on Anasayfa)."""
    return [("Ana takım  ", T_NOTE_B), ("<Parameters.Ana takım>", T_H)]


def rakip_page(d: Path, pprof: Source, footer: str, p_a: Param, p_b: Param
               ) -> tuple[list[Param], list[Source], list[Sheet], Dashboard]:
    """Opponent scouting for any two teams: projection, four-factor and shot-diet matchups, identity,
    quarters, players, rotation, fives, form and past meetings (tables from matchup.py, text from
    presentation.matchup_views)."""
    meta = pl.read_csv(d / "mx_meta.csv").row(0, named=True)
    p_court = Param("Saha", "string", "Fikstüre göre", members=[(v, None) for v in ("Fikstüre göre", "Ev", "Deplasman",
                                                                                     "Tarafsız")])
    p_roster = Param("Kadro", "string", TEAM_B, members=[(TEAM_B, None), (TEAM_A, None)])
    pair = Calc("cift", "Eşleşme", f"[team] = {{{TEAM_A}}} AND [opponent] = {RAKIP}", "boolean")
    both = Calc("iki_takim", "İki takım", f"[team] = {{{TEAM_A}}} OR [team] = {RAKIP}", "boolean")
    role = Calc("rol", "Rol", f"IF [team] = {{{TEAM_A}}} THEN 1 ELSEIF [team] = {RAKIP} THEN 2 ELSE 3 END",
                "integer", "measure", "quantitative")
    roster_team = f'(IF {{Kadro}} = "{TEAM_B}" THEN {RAKIP} ELSE {{{TEAM_A}}} END)'
    roster = Calc("kadro", "Kadro Filtresi", f"[team] = {roster_team}", "boolean")

    s_proj = Source("mx_projection.csv", "Eşleşme projeksiyonu", dimensions=("col_order", "fixture_ts"), calcs=[
        pair, Calc("cift_saha", "Eşleşme ve saha", "[Calculation_cift] AND [venue] = {Saha}", "boolean")])
    s_fac = Source("mx_factors.csv", "Dört faktör eşleşmesi", dimensions=("block_order", "factor_order", "col_order"),
                   calcs=[pair], colors={"[none:tone:nk]": {"Ana takım yönünde": GOOD, "Rakip yönünde": BAD, "Nötr": INK}})
    s_id = Source("mx_identity.csv", "Takım kimliği", dimensions=("metric_order",), calcs=[both, role])
    s_zone = Source("mx_zones.csv", "Şut diyeti", dimensions=("team_season_id", "zone_order"), calcs=[
        Calc("seri_h", "Ana takım hücumda",
             f'IF [side] = "offense" AND [team] = {{{TEAM_A}}} THEN {tag("Ana takım hücumu")} ELSEIF [side] = "defense" AND '
             f'[team] = {RAKIP} THEN "Rakibin izin verdiği" ELSEIF [side] = "league" THEN "Lig" END', "string"),
        Calc("seri_s", "Rakip hücumda",
             f'IF [side] = "offense" AND [team] = {RAKIP} THEN "Rakip hücumu" ELSEIF [side] = "defense" AND '
             f'[team] = {{{TEAM_A}}} THEN {tag("Ana takımın izin verdiği")} ELSEIF [side] = "league" THEN "Lig" END',
             "string"),
        Calc("seri_h_var", "Ana takım hücumda (filtre)", "NOT ISNULL([Calculation_seri_h])", "boolean"),
        Calc("seri_s_var", "Rakip hücumda (filtre)", "NOT ISNULL([Calculation_seri_s])", "boolean"),
    ], captions={"share": "Şut payı", "zone_label": "Bölge"},
        colors={"[none:Calculation_seri_h:nk]": series_colors(), "[none:Calculation_seri_s:nk]": series_colors()})
    s_per = Source("mx_periods.csv", "Çeyrekler", dimensions=("period_order", "metric_order"), calcs=[both, role])
    s_pl = Source("mx_players.csv", "Scout: oyuncular", dimensions=("player_order", "col_order"), calcs=[roster])
    s_rot = Source("mx_rotation.csv", "Rotasyon", dimensions=("order", "col_order"), calcs=[roster],
                   captions={"mpg": "Dakika / maç"})
    s_five = Source("mx_lineups.csv", "Beşliler", dimensions=("order", "col_order"), calcs=[roster])
    s_form = Source("mx_form.csv", "Form", dimensions=("team_season_id", "opp_id", "match_id", "week", "game_no",
                                                         "from_last"), calcs=[both, role],
                    captions={"residual": "Fark − beklenen", "game_no": "Maç"},
                    colors={"[none:tone:nk]": {"Beklentinin üstü": GOOD, "Beklentinin altı": BAD}})
    s_h2h = Source("mx_h2h.csv", "Geçmiş eşleşmeler", dimensions=("row_order",), calcs=[pair])
    pprof.calcs += [
        # the page's rule: the chosen team in its colour, the opponent in ink
        Calc("rk_vurgu", "Kadro Vurgu", f'IF [team] = {roster_team} AND [team] = {{{TEAM_A}}} THEN {tag("Seçili kadro")} '
             f'ELSEIF [team] = {roster_team} THEN "Rakip kadrosu" ELSE "Lig" END', "string"),
        Calc("rk_filtre", "Kadro + lig", f"([team] = {roster_team} AND [minutes] >= 60) OR [qualified]", "boolean"),
        Calc("rk_etiket", "Kadro etiketi", f"IF [team] = {roster_team} THEN [short] END", "string"),
    ]
    pprof.colors["[none:Calculation_rk_vurgu:nk]"] = {**tinted("Seçili kadro"), "Rakip kadrosu": INK, "Lig": LOW}

    head = {"fontname": "Tableau Semibold", "fontsize": "15", "fontcolor": INK}
    r1 = Sheet("R1 Projeksiyon", s_proj, cols=[F("col_label")], mark="Text", label=[F("value_text")],
               detail=[F("title_text", "attr", "nk"), F("fixture_text", "attr", "nk")], tooltip=TERM_FIELDS,
               filters=[("true", F("Calculation_cift_saha"))],
               tooltip_runs=tip(*TERM_TIP, NL, "<[none:col_label:nk]>", ": ", "<[none:value_text:nk]>"),
               sorts=[(F("col_label"), F("col_order", "min", "ok"), "ASC", "columns")],
               title=[("<[attr:title_text:nk]>", head), NL, ("<[attr:fixture_text:nk]>", T_SHEET2)])
    r2 = Sheet("R2 Dört faktör eşleşmesi", s_fac, rows=[F("block"), F("factor_label")], cols=[F("col")], mark="Text",
               label=[F("value_text")], color=F("tone"), filters=[("true", F("Calculation_cift"))],
               tooltip=[*TERM_FIELDS, F("col_desc")],
               tooltip_runs=tip(*TERM_TIP, NL, "<[none:col:nk]>", ": ", "<[none:value_text:nk]>", NL,
                                "<[none:col_desc:nk]>"),
               sorts=[(F("block"), F("block_order", "min", "ok"), "ASC", "rows"),
                      (F("factor_label"), F("factor_order", "min", "ok"), "ASC", "rows"),
                      (F("col"), F("col_order", "min", "ok"), "ASC", "columns")],
               title=title("Dört faktör eşleşmesi", "Beklenen = hücum eden × savunmanın izni / lig · etki: 100 hücumda "
                           "sayı · mavi: ana takım yönünde, kırmızı: rakip yönünde · sayı yanında lig sırası"))
    r3 = Sheet("R3 Takım kimliği", s_id, rows=[F("metric")], cols=[F("team_short")], mark="Text",
               label=[F("value_text")], filters=[("true", F("Calculation_iki_takim"))], tooltip=TERM_FIELDS,
               tooltip_runs=tip(*TERM_TIP, NL, "<[none:team_short:nk]>", ": ", "<[none:value_text:nk]>"),
               sorts=[(F("metric"), F("metric_order", "min", "ok"), "ASC", "rows"),
                      (F("team_short"), F("Calculation_rol", "min", "qk"), "ASC", "columns")],
               title=title("Kimlik", "Sezon değeri · lig sırası · ● 8 maçta oturdu ◐ kısmen ○ gürültü"))

    def diet(name, calc, flt, heading, sub):
        # a dot per series on one line per zone: the three shares side by side without stacking
        return Sheet(name, s_zone, rows=[F("zone_label")], cols=[F("share", "sum", "qk")], mark="Circle",
                     color=F(calc), detail=[F("share_text")], tooltip=[F("fg_text"), F("xpts_text"), F("shots", "sum", "qk")],
                     filters=[("true", F(flt))], mark_size=1.6,
                     sorts=[(F("zone_label"), F("zone_order", "min", "ok"), "ASC", "rows")],
                     axes=[Axis(F("share", "sum", "qk"), "cols", 0, 0.45, title="Şutların payı", fmt=FMT_P0)],
                     formats={"[sum:share:qk]": FMT_P0},
                     title=title(heading, sub),
                     tooltip_runs=tip(f"<[none:{calc}:nk]>", " · ", "<[none:zone_label:nk]>", NL, "Pay ",
                                      "<[none:share_text:nk]>", " · isabet ", "<[none:fg_text:nk]>", NL,
                                      "xPTS/şut ", "<[none:xpts_text:nk]>", " · ", "<[sum:shots:qk]>", " şut")
                     + defs("zone_share", "xpts_per_shot"))
    r4 = diet("R4 Şut diyeti: ana takım hücumda", "Calculation_seri_h", "Calculation_seri_h_var",
              "Ana takım hücumda: nereden atıyor, rakip nereye izin veriyor",
              "Takım renginde: ana takımın şutları · koyu: rakibin rakiplerine verdiği · gri: lig · izin verilen isabet gürültü, payı oturur")
    r5 = diet("R5 Şut diyeti: rakip hücumda", "Calculation_seri_s", "Calculation_seri_s_var",
              "Rakip hücumda: nereden atıyor, ana takım nereye izin veriyor",
              "Siyah: rakibin şutları · açık renk: ana takımın rakiplerine verdiği · gri: lig")
    r6 = Sheet("R6 Çeyrekler", s_per, rows=[F("period")], cols=[F("team_short"), F("metric")], mark="Text",
               label=[F("value_text")], filters=[("true", F("Calculation_iki_takim"))], tooltip=TERM_FIELDS,
               tooltip_runs=tip(*TERM_TIP, NL, "<[none:team_short:nk]>", " · ", "<[none:period:nk]>", ": ",
                                "<[none:value_text:nk]>"),
               sorts=[(F("period"), F("period_order", "min", "ok"), "ASC", "rows"),
                      (F("team_short"), F("Calculation_rol", "min", "qk"), "ASC", "columns"),
                      (F("metric"), F("metric_order", "min", "ok"), "ASC", "columns")],
               title=title("Çeyrek profili", "Maç başına atılan, yenen, fark · 1. çeyrek farkı 8 maçta ◐"))
    r7 = Sheet("R7 Oyuncular", s_pl, rows=[F("player_label")], cols=[F("col")], mark="Text", label=[F("value_text")],
               detail=[F("player"), F("team")], filters=[("true", F("Calculation_kadro"))], tooltip=TERM_FIELDS,
               tooltip_runs=tip(*TERM_TIP, NL, "<[none:player:nk]>", ": ", "<[none:value_text:nk]>"),
               sorts=[(F("player_label"), F("player_order", "min", "ok"), "ASC", "rows"),
                      (F("col"), F("col_order", "min", "ok"), "ASC", "columns")],
               title=title("Oyuncular · ", "Bir oyuncuya tıklayın: profili Oyuncular sayfasında açılır · dengeli: lig "
                           "ortalaması ve geçen sezonla harmanlanmış, "
                           "± %90 aralığın yarısı (puan)", [("<Parameters.Kadro>", T_SHEET)]))
    r8 = Sheet("R8 Kullanım × TS (kadro)", pprof, cols=[F("usg", "avg", "qk")], rows=[F("ts", "avg", "qk")], mark="Circle",
               color=F("Calculation_rk_vurgu"), label=[F("Calculation_rk_etiket")], label_cull=False,
               size=F("minutes", "sum", "qk"), detail=[F("player"), F("team")],
               filters=[("true", F("Calculation_rk_filtre"))],
               axes=[Axis(F("usg", "avg", "qk"), "cols", 0.06, 0.36, title="USG% → daha çok top", fmt=FMT_P0),
                     Axis(F("ts", "avg", "qk"), "rows", 0.3, 0.8, title="TS% ↑ verimli", fmt=FMT_P0)],
               reflines=[RefLine(F("usg", "avg", "qk"), 0.2), RefLine(F("ts", "avg", "qk"), 0.55)],
               formats={"[avg:usg:qk]": FMT_P1, "[avg:ts:qk]": FMT_P1}, mark_size=0.6,
               title=title("Hücumu kim taşıyor", "Renkli: seçili kadro (60+ dk) · gri: ligin yeterli süreli oyuncuları · tıklayın: profil"),
               tooltip_runs=tip("<[none:player:nk]>", NL, "USG% ", "<[avg:usg:qk]>", "  ·  TS% ", "<[avg:ts:qk]>", NL,
                                "<[sum:minutes:qk]>", " dakika") + defs("usg", "ts"))
    r9 = Sheet("R9 Rotasyon", s_rot, rows=[F("player_label")], cols=[F("col")], mark="Square",
               color=F("mpg", "sum", "qk"), color_seq="blue_10_0", label=[F("value_text")],
               tooltip=[F("starter_text"), F("term_desc")], detail=[F("player"), F("team")],
               filters=[("true", F("Calculation_kadro"))],
               sorts=[(F("player_label"), F("order", "min", "ok"), "ASC", "rows"),
                      (F("col"), F("col_order", "min", "ok"), "ASC", "columns")],
               title=title("Rotasyon · ", "Takım maçı başına dakika, çeyrek çeyrek · son sütun: 4. çeyreğin son 5 dakikası "
                           "(maçı kim kapatıyor)", [("<Parameters.Kadro>", T_SHEET)]),
               tooltip_runs=tip("<[none:player_label:nk]>", " · ", "<[none:col:nk]>", NL, "<[none:value_text:nk]>",
                                " dk/maç", NL, "<[none:starter_text:nk]>", NL, "<[none:term_desc:nk]>"))
    r10 = Sheet("R10 Beşliler", s_five, rows=[F("lineup_label")], cols=[F("col")], mark="Text", label=[F("value_text")],
                tooltip=[F("lineup_names"), *TERM_FIELDS], filters=[("true", F("Calculation_kadro"))],
                tooltip_runs=tip(*TERM_TIP, NL, "<[none:lineup_names:nk]>", ": ", "<[none:value_text:nk]>"),
                sorts=[(F("lineup_label"), F("order", "min", "ok"), "ASC", "rows"),
                       (F("col"), F("col_order", "min", "ok"), "ASC", "columns")],
                title=title("En çok oynayan beşliler · ", "Soyadının ilk üç harfi · net reyting az hücumla çok oynar (○): "
                            "dakikalar koçun tercihidir",
                            [("<Parameters.Kadro>", T_SHEET)]))
    r11 = Sheet("R11 Form", s_form, rows=[F("team_short"), F("residual", "sum", "qk")], cols=[F("game_no", "none", "ok")],
                mark="Bar", color=F("tone"), label=[F("game_label")],
                tooltip=[F("score_text"), F("expected_text"), F("residual_text")],
                filters=[("true", F("Calculation_iki_takim"))],
                sorts=[(F("team_short"), F("Calculation_rol", "min", "qk"), "ASC", "rows")],
                axes=[Axis(F("residual", "sum", "qk"), "rows", title="Fark − beklenen", fmt=FMT_0)],
                reflines=[RefLine(F("residual", "sum", "qk"), 0.0)], formats={"[sum:residual:qk]": FMT_1},
                title=title("Form: beklentinin üstünde mi?", "Her maç: skor farkı − sezon reytinglerinin o rakip ve sahaya "
                            f"göre beklediği · tek maçta ±{num(meta['sigma'] or 12, 0)} sayı şans normaldir"),
                tooltip_runs=tip("<[none:game_label:nk]>", "  ", "<[none:score_text:nk]>", NL, "Beklenen fark ",
                                 "<[none:expected_text:nk]>", " · sapma ", "<[none:residual_text:nk]>") + defs("form"))
    r12 = Sheet("R12 Geçmiş eşleşmeler", s_h2h, rows=[F("row")], mark="Text",
                label=[F("score_text"), F("result")], filters=[("true", F("Calculation_cift"))],
                sorts=[(F("row"), F("row_order", "min", "ok"), "ASC", "rows")],
                title=title("Geçmiş eşleşmeler", "Bu ve geçen sezon · ana takımın gözünden · kadrolar değişmiş olabilir"))

    s_keys = Source("mx_keys.csv", "Öne çıkan eşleşmeler", dimensions=("key_order", "score"), calcs=[pair])
    s_style = Source("mx_style.csv", "Oyun tarzı", dimensions=("metric_order",), calcs=[
        both, role,
        Calc("stil_rol", "Takım", f'IF [team] = {{{TEAM_A}}} THEN {tag("Ana takım")} ELSEIF [team] = {RAKIP} '
             'THEN "Rakip" END', "string"),
        Calc("stil_etiket", "Rakip", f'IF [team] = {RAKIP} THEN [desc] + " · " + [value_text] END', "string")],
        colors={"[none:Calculation_stil_rol:nk]": team_colors()})
    s_cards = Source("mx_roles.csv", "Oyuncu kartları", dimensions=("card_order",),
                     calcs=[Calc("rakip_kadro", "Rakip kadrosu", f"[team] = {RAKIP}", "boolean")])
    lines = [F("l1"), F("l2"), F("l3"), F("l4")]
    rk1 = Sheet("R0 Maçın anahtarları", s_keys, cols=[F("key")], mark="Text", label=[*lines, F("l5")],
                filters=[("true", F("Calculation_cift"))],
                sorts=[(F("key"), F("key_order", "min", "ok"), "ASC", "columns")],
                title=title("Öne çıkan eşleşmeler"),
                tooltip_runs=tip("<[none:key:nk]>", NL, "<[none:l1:nk]>", NL, "<[none:l2:nk]>", NL, "<[none:l3:nk]>",
                                 NL, "<[none:l4:nk]>", NL, "<[none:l5:nk]>"))
    rk2 = Sheet("R0 Oyun tarzı", s_style, rows=[F("metric")], cols=[F("pct", "avg", "qk")], mark="Circle",
                color=F("Calculation_stil_rol"), label=[F("Calculation_stil_etiket")], label_cull=False,
                detail=[F("team_short"), F("value_text"), F("desc")], filters=[("true", F("Calculation_iki_takim"))],
                sorts=[(F("metric"), F("metric_order", "min", "ok"), "ASC", "rows")],
                axes=[Axis(F("pct", "avg", "qk"), "cols", -4, 104,
                           title="Ligdeki yeri (0 en az / en kötü, 100 en çok / en iyi) · çizgi: lig ortası", fmt=FMT_0)],
                reflines=[RefLine(F("pct", "avg", "qk"), 50.0)], formats={"[avg:pct:qk]": FMT_0},
                mark_size=1.3,
                title=title("Oyun tarzı", "Renkli: ana takım · koyu: rakip, yanında rakibin değeri · yalnız 8 maçta "
                            "oturan (●) ya da kısmen oturan (◐) göstergeler"),
                tooltip_runs=tip("<[none:team_short:nk]>", " · ", "<[none:metric:nk]>", NL, "<[none:value_text:nk]>",
                                 " · ", "<[none:desc:nk]>", NL, "Ligde yüzdelik: ", "<[avg:pct:qk]>"))
    rk3 = Sheet("R0 Kimi durdurmalıyız", s_cards, cols=[F("card")], mark="Text", label=lines,
                detail=[F("player")], filters=[("true", F("Calculation_rakip_kadro"))],
                sorts=[(F("card"), F("card_order", "min", "ok"), "ASC", "columns")],
                title=title("Rakibin en çok oynayan beşi", "Etiket: ligin yeterli süreli oyuncuları arasında ilk %20 · "
                            "TS/3P dengeli, ± %90 aralığın yarısı · tıklayın: oyuncu profili"),
                tooltip_runs=tip("<[none:player:nk]>", NL, "<[none:l1:nk]>", NL, "<[none:l2:nk]>", NL, "<[none:l3:nk]>",
                                 NL, "<[none:l4:nk]>"))
    sources = [s_proj, s_fac, s_id, s_zone, s_per, s_pl, s_rot, s_five, s_form, s_h2h, s_keys, s_style, s_cards]
    sheets = [r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r11, r12, rk1, rk2, rk3]
    games_now = int(meta["games_now"] or 0)
    method = [("Yöntem · ", T_NOTE_B), (
        f"Reytingler rakip gücüne ve sahaya göre düzeltildi (ridge regresyon, hücum sayısıyla ağırlıklı). Projeksiyon: "
        f"tempo = iki takımın temposu çarpımı / lig, fark ± %80 aralık, kazanma olasılığı normal dağılımla "
        f"(σ = {num(meta['sigma'] or 12, 1)} sayı, {meta['sigma_from']}). Ev avantajı bu sezon "
        f"{num(meta['home_pts'] or 0, 1, True)} sayı. Dört faktör ağırlıkları (100 hücumda sayı / 1 puan): "
        f"{meta['weights_text']}. Güven işaretleri: takım istatistiğinin {games_now} maçta ne kadarının gerçek fark "
        f"olduğu (sınıf içi korelasyon, {meta['reliability_from']}).", T_NOTE)]
    check = [("Tahmin ne kadar tutuyor · ", T_NOTE_B),
             (" · ".join(x for x in (meta["backtest_text"], meta["prior_text"]) if isinstance(x, str) and x), T_NOTE)]
    zones = frame("Rakip analizi", "Ana takım × rakip (varsayılan: sıradaki rakip) · maç öncesi teknik scout: eşleşme, "
                  "şut diyeti, oyuncular, rotasyon",
                  "Yukarıdan aşağı: maç, öne çıkan eşleşmeler, rakibin oyun tarzı, oyuncular, ayrıntı. Sayılar sezon "
                  "eğilimidir; karar koçundur. ● 8 maçta oturmuş, ◐ kısmen, ○ büyük ölçüde şans.", footer, height=RAKIP_H) + [
        Zone("text", 16, 76, 260, 36, runs=team_line()), Zone("param", 288, 72, 260, 42, param=p_b),
        Zone("param", 560, 72, 180, 42, param=p_court), Zone("param", 752, 72, 160, 42, param=p_roster),
        # 1 · the game: what the numbers expect and how far to trust it
        section(1, "Maç kartı", "Beklenen skor ve fark, rakiplerin gücüne ve sahaya göre düzeltilmiş reytinglerden", 144),
        Zone("sheet", 16, 190, 1184 - 2 * MATCH_LOGO - 28 - 28, 126, sheet=r1.name),
        *matchup_logos(200, "Logo · ana takım (maç)", "Logo · rakip"),
        Zone("text", 16, 320, 1168, 34, runs=check),
        # 2 · stand-out matchups (scout reports: three to five keys, in priority order) and the busiest player
        section(2, "Öne çıkan eşleşmeler", "Sezon eğilimlerinin en çok fark yaratabileceği ve verisi en sağlam yerler "
                "önce · sıra: en yüksek değerden (1. = ligde en yüksek) · birleşim: iki takımın eğilimi bir arada · "
                "sezon eğilimidir, tek maçı tahmin etmez", 364),
        Zone("sheet", 16, 410, 1168, 182, sheet=rk1.name, fit="fit-width", show_title=False),
        # 3 · how they play: team system before individuals
        section(3, "Rakip nasıl oynuyor", "Önce sistem: iki takımın lig içindeki yeri · renkli ana takım, koyu rakip "
                "(yanında rakibin değeri) · yalnız oturmuş (●) ve kısmen oturmuş (◐) göstergeler", 596),
        Zone("sheet", 16, 642, 1168, 330, sheet=rk2.name, show_title=False),
        # 4 · the players who stand out
        section(4, "Rakibin öne çıkan oyuncuları", "En çok oynayan beş · etiket: ligin yeterli süreli oyuncuları "
                "arasında ilk %20 · dakika ve kullanım 1–2 maçta oturur, isabet şansla oynar · tıklayın: profil", 984),
        Zone("sheet", 16, 1030, 1168, 150, sheet=rk3.name, fit="fit-width", show_title=False),
        Zone("sheet", 16, 1190, 768, 340, sheet=r7.name, fit="fit-width"),
        Zone("sheet", 796, 1190, 388, 340, sheet=r8.name),
        # 5 · the matchup in detail
        section(5, "Eşleşme ayrıntısı", "Dört faktörün tamamı ve şut bölgeleri: anahtarların dayandığı sayılar", 1542),
        Zone("sheet", 16, 1588, 1168, 330, sheet=r2.name),
        Zone("sheet", 16, 1928, 578, 300, sheet=r4.name),
        Zone("sheet", 606, 1928, 578, 300, sheet=r5.name),
        # 6 · rotation
        section(6, "Rotasyon ve kapanış", "Dakikalar koçun tercihidir ve hemen oturur; beşli neti ise az hücumla çok oynar",
                2216),
        Zone("sheet", 16, 2286, 578, 340, sheet=r9.name),
        Zone("sheet", 606, 2286, 578, 340, sheet=r10.name, fit="fit-width"),
        # 7 · form and history
        section(7, "Form ve geçmiş", "Son maçlar beklentiye göre, çeyrekler ve iki takımın son iki sezondaki maçları",
                2614),
        Zone("sheet", 16, 2684, 1168, 220, sheet=r11.name),
        Zone("sheet", 16, 2914, 578, 180, sheet=r6.name),
        Zone("sheet", 606, 2914, 578, 180, sheet=r12.name),
        Zone("text", 16, 3104, 1168, 46, runs=method),
    ]
    return [p_court, p_roster], sources, sheets, Dashboard("Rakip Analizi", zones, height=RAKIP_H)


# -- Anasayfa ---------------------------------------------------------------------------------
HOME_H = 1100
HOME_TILES = [("Takımlar", "Ligde nerede: reytingler, dört faktör"), ("Şut Kalitesi", "Nereden atıyor, ne kadar iyi"),
              ("Oyuncular", "Oyuncu profili ve yüzdelikler"), ("İkililer", "Kim kiminle iyi, asist ağı"),
              ("Maçlar", "Son maç dakika dakika"), ("Güven", "Hangi sayıya ne kadar güvenilir"),
              ("Rakip Analizi", "Sıradaki rakibin scout'u"), ("Yöntem", "Veri, yöntem, sözlük")]


def home_page(d: Path, rk_sources: list[Source], p_team: Param, footer: str, sub: str
              ) -> tuple[list[Source], list[Sheet], Dashboard]:
    """The team is chosen here once; every other page is built around it. Shows the next game, the
    numbers that have settled after this many games (●), the table, form and the most used players."""
    src = {s.file: s for s in rk_sources}
    s_proj, s_form, s_pl, s_id = (src[f] for f in ("mx_projection.csv", "mx_form.csv", "mx_players.csv",
                                                   "mx_identity.csv"))
    s_proj.calcs.append(Calc("sonraki", "Sıradaki maç",
                             f'[team] = {{Ana takım}} AND [opponent] = {RAKIP} AND [venue] = "Fikstüre göre"', "boolean"))
    s_id.calcs.append(Calc("oturan", "Oturmuş gösterge", '[team] = {Ana takım} AND [tier] = "oturdu"', "boolean"))
    s_form.calcs.append(Calc("ana_form", "Ana takım formu", "[team] = {Ana takım}", "boolean"))
    s_pl.calcs.append(Calc("ana_kadro", "Ana takım kadrosu",
                           '[team] = {Ana takım} AND ([col] = "Dk/maç" OR [col] = "Maç (ilk 5)" OR [col] = "USG%" OR '
                           '[col] = "TS% dengeli" OR [col] = "3PA oranı" OR [col] = "AST%")', "boolean"))
    s_tab = Source("home_table.csv", "Puan durumu", dimensions=("rank", "col_order"), calcs=[
        Calc("tablo_vurgu", "Vurgu", f'IF [team] = {{Ana takım}} THEN {tag("Ana takım")} ELSEIF [team] = {RAKIP} THEN "Rakip" '
             'ELSE "Diğer" END', "string")],
        colors={"[none:Calculation_tablo_vurgu:nk]": {**tinted("Ana takım"), "Rakip": INK, "Diğer": SEC}})
    head = {"fontname": "Tableau Semibold", "fontsize": "15", "fontcolor": INK}

    a1 = Sheet("A1 Sıradaki maç", s_proj, cols=[F("col_label")], mark="Text", label=[F("value_text")],
               detail=[F("title_text", "attr", "nk"), F("fixture_text", "attr", "nk")], tooltip=TERM_FIELDS,
               filters=[("true", F("Calculation_sonraki"))],
               sorts=[(F("col_label"), F("col_order", "min", "ok"), "ASC", "columns")],
               title=[("<[attr:title_text:nk]>", head), NL, ("<[attr:fixture_text:nk]>", T_SHEET2)],
               tooltip_runs=tip(*TERM_TIP, NL, "<[none:col_label:nk]>", ": ", "<[none:value_text:nk]>"))
    a2 = Sheet("A2 Temel göstergeler", s_id, rows=[F("metric")], mark="Text", label=[F("value_text")],
               tooltip=TERM_FIELDS, filters=[("true", F("Calculation_oturan"))],
               sorts=[(F("metric"), F("metric_order", "min", "ok"), "ASC", "rows")],
               title=title("Temel göstergeler", "Yalnız 8 maçta oturmuş olanlar (●) · değer · lig sırası"),
               tooltip_runs=tip(*TERM_TIP, NL, "<[none:value_text:nk]>"))
    a3 = Sheet("A3 Puan durumu", s_tab, rows=[F("rank", "none", "ok")], cols=[F("col")], mark="Text",
               label=[F("value_text")], color=F("Calculation_tablo_vurgu"), tooltip=TERM_FIELDS,
               sorts=[
                      (F("col"), F("col_order", "min", "ok"), "ASC", "columns")],
               title=title("Puan durumu", "Renkli: ana takım · koyu: rakip · güç sırası fikstürün zorluğunu hesaba katar"),
               tooltip_runs=tip(*TERM_TIP, NL, "<[none:row:nk]>", ": ", "<[none:value_text:nk]>"))
    a4 = Sheet("A4 Form", s_form, rows=[F("residual", "sum", "qk")], cols=[F("game_no", "none", "ok")], mark="Bar",
               color=F("tone"), label=[F("game_label")], tooltip=[F("score_text"), F("expected_text"), F("residual_text")],
               filters=[("true", F("Calculation_ana_form"))],
               axes=[Axis(F("residual", "sum", "qk"), "rows", title="Fark − beklenen", fmt=FMT_0)],
               reflines=[RefLine(F("residual", "sum", "qk"), 0.0)], formats={"[sum:residual:qk]": FMT_1},
               title=title("Form", "Her maç: skor farkı − rakibe ve sahaya göre beklenen · mavi beklentinin üstü"),
               tooltip_runs=tip("<[none:game_label:nk]>", "  ", "<[none:score_text:nk]>", NL, "Beklenen fark ",
                                "<[none:expected_text:nk]>", " · sapma ", "<[none:residual_text:nk]>") + defs("form"))
    a5 = Sheet("A5 Kadro", s_pl, rows=[F("player_label")], cols=[F("col")], mark="Text", label=[F("value_text")],
               detail=[F("player")], tooltip=TERM_FIELDS, filters=[("true", F("Calculation_ana_kadro"))],
               sorts=[(F("player_label"), F("player_order", "min", "ok"), "ASC", "rows"),
                      (F("col"), F("col_order", "min", "ok"), "ASC", "columns")],
               title=title("Kadro", "En çok süre alan 10 · bir oyuncuya tıklayın: profili açılır"),
               tooltip_runs=tip(*TERM_TIP, NL, "<[none:player:nk]>", ": ", "<[none:value_text:nk]>"))

    zones = frame("Anasayfa", sub + " · takımı burada bir kez seçin, bütün sayfalar ona göre açılır", None, footer,
                  height=HOME_H) + [
        Zone("param", 16, 76, 320, 42, param=p_team),
        Zone("text", 348, 80, 836, 36, runs=[("Seçtiğiniz takım bütün sayfaların ana takımıdır. Rakip, fikstürdeki "
                                              "sıradaki maçtan gelir; Takımlar ya da Rakip Analizi'nde değiştirebilirsiniz.",
                                              T_NOTE)]),
        Zone("sheet", 16, 128, 1184 - 2 * MATCH_LOGO - 28 - 28, 118, sheet=a1.name),
        *matchup_logos(140, "Logo · ana takım (maç)", "Logo · rakip"),
        Zone("sheet", 16, 256, 340, 440, sheet=a2.name),
        Zone("sheet", 368, 256, 440, 440, sheet=a3.name, fit="fit-width"),
        Zone("sheet", 820, 256, 364, 440, sheet=a4.name),
        Zone("sheet", 16, 708, 760, 360, sheet=a5.name, fit="fit-width"),
        Zone("text", 788, 708, 396, 26, runs=[("Sayfalar", T_H)]),
    ]
    for i, (target, desc) in enumerate(HOME_TILES):
        y = 740 + i * 40
        zones.append(Zone("nav", 788, y, 130, 32, target=target, title=NAV_TITLE.get(target, target)))
        zones.append(Zone("text", 926, y + 6, 258, 26, runs=[(desc, T_NOTE)]))
    return [s_tab], [a1, a2, a3, a4, a5], Dashboard("Anasayfa", zones, height=HOME_H)
