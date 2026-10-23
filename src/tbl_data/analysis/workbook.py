"""A ready Tableau workbook (.twb) over the Tableau CSVs: worksheets and dashboards.

Tableau has no API for building views, so the XML is written here, following what
Tableau Public 2025.3 saves (document version 18.1). The court background image is not
set: Tableau's XML for it is not documented, so it is one manual step (docs/tableau.md).
"""

from __future__ import annotations

import hashlib
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

USER_NS = "http://www.tableausoftware.com/xml/user"
ET.register_namespace("user", USER_NS)
U = "{%s}" % USER_NS
VERSION = "18.1"
BUILD = "2025.3.1 (20253.25.1210.1815)"


def _id(*parts: str, n: int = 28) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:n]


def _uuid(*parts: str) -> str:
    return "{" + str(uuid.UUID(hashlib.md5("|".join(parts).encode()).hexdigest())).upper() + "}"


@dataclass
class Calc:
    key: str  # internal name, Calculation_<key>
    caption: str
    formula: str
    datatype: str  # string/integer/real/boolean/date
    role: str  # dimension/measure
    type: str  # nominal/ordinal/quantitative

    @property
    def name(self) -> str:
        return f"Calculation_{self.key}"


@dataclass
class Source:
    file: str  # CSV file name in the export directory
    caption: str
    dimensions: tuple[str, ...] = ()  # numeric columns to treat as dimensions
    calcs: list[Calc] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)  # filled from the CSV: name -> datatype

    @property
    def name(self) -> str:
        return "federated." + _id("ds", self.file)

    def kind(self, col: str) -> tuple[str, str, str]:
        """(datatype, role, type) of a column or calc."""
        for c in self.calcs:
            if col == c.name:
                return c.datatype, c.role, c.type
        dt = self.columns[col]
        if dt in ("string", "boolean", "date"):
            return dt, "dimension", "nominal" if dt != "date" else "ordinal"
        if col in self.dimensions:
            return dt, "dimension", "quantitative" if dt == "real" else "ordinal"
        return dt, "measure", "quantitative"


@dataclass
class F:
    """A field on a shelf: column + derivation (none/sum/avg/cnt/usr) + n(ominal) / o(rdinal) / q(uant.)."""
    col: str
    deriv: str = "none"
    kind: str = "nk"  # nk / ok / qk

    def instance(self) -> str:
        return f"[{self.deriv}:{self.col}:{self.kind}]"


@dataclass
class Sheet:
    name: str
    source: Source
    rows: list[F] = field(default_factory=list)
    cols: list[F] = field(default_factory=list)
    mark: str = "Automatic"
    color: F | None = None
    size: F | None = None
    label: list[F] = field(default_factory=list)
    detail: list[F] = field(default_factory=list)
    tooltip: list[F] = field(default_factory=list)
    aggregate: bool = True
    # (field, kind, value): kind 'all' / 'member' (value = member) / 'min' (value = number)
    filters: list[tuple[F, str, object]] = field(default_factory=list)
    axes: list[tuple[F, str, float, float]] = field(default_factory=list)  # (field, rows/cols, min, max)
    title: str | None = None


DATATYPES = {pl.Int8: "integer", pl.Int16: "integer", pl.Int32: "integer", pl.Int64: "integer", pl.UInt32: "integer",
             pl.UInt64: "integer", pl.Float32: "real", pl.Float64: "real", pl.Boolean: "boolean", pl.Date: "date",
             pl.String: "string"}


def _read_columns(src: Source, directory: Path) -> None:
    df = pl.read_csv(directory / src.file, n_rows=2000, try_parse_dates=True, infer_schema_length=2000)
    src.columns = {c: DATATYPES.get(df.schema[c].base_type(), "string") for c in df.columns}


def _datasource(src: Source, directory: Path) -> ET.Element:
    table = src.file.replace(".csv", "#csv")
    conn_name = "textscan." + _id("conn", src.file)
    ds = ET.Element("datasource", caption=src.caption, inline="true", name=src.name, version=VERSION)
    fed = ET.SubElement(ds, "connection", {"class": "federated"})
    named = ET.SubElement(ET.SubElement(fed, "named-connections"), "named-connection", caption=src.caption, name=conn_name)
    ET.SubElement(named, "connection", {"class": "textscan", "directory": str(directory), "filename": src.file,
                                        "server": "", "workgroup-auth-mode": "as-is"})

    def relation(parent, with_conn=True):
        attrs = {"name": src.file, "table": f"[{table}]", "type": "table"}
        if with_conn:
            attrs = {"connection": conn_name, **attrs}
        rel = ET.SubElement(parent, "relation", attrs)
        cols = ET.SubElement(rel, "columns", {"character-set": "UTF-8", "header": "yes", "locale": "en", "separator": ","})
        for i, (c, dt) in enumerate(src.columns.items()):
            ET.SubElement(cols, "column", datatype=dt, name=c, ordinal=str(i))
        return rel

    relation(fed)
    ET.SubElement(ds, "aliases", enabled="yes")
    for c in src.calcs:
        el = ET.SubElement(ds, "column", caption=c.caption, datatype=c.datatype, name=f"[{c.name}]", role=c.role,
                           type=c.type)
        ET.SubElement(el, "calculation", {"class": "tableau", "formula": c.formula})
    for c in src.columns:
        dt, role, typ = src.kind(c)
        ET.SubElement(ds, "column", datatype=dt, name=f"[{c}]", role=role, type=typ)
    ET.SubElement(ds, "layout", {"dim-ordering": "alphabetic", "measure-ordering": "alphabetic", "show-structure": "true"})
    obj = ET.SubElement(ET.SubElement(ET.SubElement(ds, "object-graph"), "objects"), "object",
                        caption=src.file, id=f"{src.file}_{_id('obj', src.file, n=32).upper()}")
    relation(ET.SubElement(obj, "properties", context=""))
    return ds


def _worksheet(sh: Sheet) -> ET.Element:
    src, dsn = sh.source, sh.source.name
    q = lambda f: f"[{dsn}].{f.instance()}"  # noqa: E731
    ws = ET.Element("worksheet", name=sh.name)
    if sh.title:
        layout = ET.SubElement(ws, "layout-options")
        title = ET.SubElement(ET.SubElement(layout, "title"), "formatted-text")
        ET.SubElement(title, "run").text = sh.title
    table = ET.SubElement(ws, "table")
    view = ET.SubElement(table, "view")
    ET.SubElement(ET.SubElement(view, "datasources"), "datasource", caption=src.caption, name=dsn)
    deps = ET.SubElement(view, "datasource-dependencies", datasource=dsn)
    used = [*sh.rows, *sh.cols, *(x for x in (sh.color, sh.size) if x), *sh.label, *sh.detail, *sh.tooltip,
            *(f for f, _, _ in sh.filters)]
    seen_cols, seen_inst = set(), set()
    for f in used:
        if f.col not in seen_cols:
            seen_cols.add(f.col)
            dt, role, typ = src.kind(f.col)
            el = ET.SubElement(deps, "column", datatype=dt, name=f"[{f.col}]", role=role, type=typ)
            calc = next((c for c in src.calcs if c.name == f.col), None)
            if calc:
                el.set("caption", calc.caption)
                ET.SubElement(el, "calculation", {"class": "tableau", "formula": calc.formula})
    for f in used:
        if f.instance() not in seen_inst:
            seen_inst.add(f.instance())
            deriv = {"none": "None", "sum": "Sum", "avg": "Avg", "cnt": "Count", "usr": "User", "max": "Max",
                     "attr": "Attribute"}[f.deriv]
            ET.SubElement(deps, "column-instance", column=f"[{f.col}]", derivation=deriv, name=f.instance(),
                          pivot="key", type={"nk": "nominal", "ok": "ordinal", "qk": "quantitative"}[f.kind])
    for f, kind, value in sh.filters:
        if kind == "min":
            flt = ET.SubElement(view, "filter", {"class": "quantitative", "column": q(f), "included-values": "in-range"})
            ET.SubElement(flt, "min").text = str(value)
        else:
            flt = ET.SubElement(view, "filter", {"class": "categorical", "column": q(f)})
            if kind == "all":
                ET.SubElement(flt, "groupfilter", {"function": "level-members", "level": f.instance(),
                                                   U + "ui-enumeration": "all", U + "ui-marker": "enumerate"})
            else:
                ET.SubElement(flt, "groupfilter", {"function": "member", "level": f.instance(),
                                                   "member": value if value in ("true", "false") else f'"{value}"',
                                                   U + "ui-domain": "database", U + "ui-enumeration": "inclusive",
                                                   U + "ui-marker": "enumerate"})
    ET.SubElement(view, "aggregation", value="true" if sh.aggregate else "false")
    style = ET.SubElement(table, "style")
    if sh.axes:
        rule = ET.SubElement(style, "style-rule", element="axis")
        for f, scope, lo, hi in sh.axes:
            ET.SubElement(rule, "encoding", {"attr": "space", "class": "0", "field": q(f), "field-type": "quantitative",
                                             "max": str(hi), "min": str(lo), "range-type": "fixed", "scope": scope,
                                             "type": "space"})
    pane = ET.SubElement(ET.SubElement(table, "panes"), "pane", {"selection-relaxation-option": "selection-relaxation-allow"})
    ET.SubElement(ET.SubElement(pane, "view"), "breakdown", value="auto")
    ET.SubElement(pane, "mark", {"class": sh.mark})
    enc = ET.SubElement(pane, "encodings")
    if sh.color:
        ET.SubElement(enc, "color", column=q(sh.color))
    if sh.size:
        ET.SubElement(enc, "size", column=q(sh.size))
    for f in sh.label:
        ET.SubElement(enc, "text", column=q(f))
    for f in sh.detail:
        ET.SubElement(enc, "lod", column=q(f))
    for f in sh.tooltip:
        ET.SubElement(enc, "tooltip", column=q(f))
    if sh.label:
        ET.SubElement(ET.SubElement(ET.SubElement(pane, "style"), "style-rule", element="mark"), "format",
                      attr="mark-labels-show", value="true")
    ET.SubElement(table, "rows").text = " / ".join(q(f) for f in sh.rows) if len(sh.rows) < 2 else \
        "(" + " / ".join(q(f) for f in sh.rows) + ")"
    ET.SubElement(table, "cols").text = " / ".join(q(f) for f in sh.cols) if len(sh.cols) < 2 else \
        "(" + " / ".join(q(f) for f in sh.cols) + ")"
    ET.SubElement(ws, "simple-id", uuid=_uuid("ws", sh.name))
    return ws


def _window_ws(sh: Sheet) -> ET.Element:
    w = ET.Element("window", {"class": "worksheet", "name": sh.name})
    cards = ET.SubElement(w, "cards")
    left = ET.SubElement(ET.SubElement(cards, "edge", name="left"), "strip", size="160")
    for t in ("pages", "filters", "marks"):
        ET.SubElement(left, "card", type=t)
    top = ET.SubElement(cards, "edge", name="top")
    for t, size in (("columns", "2147483647"), ("rows", "2147483647"), ("title", "31")):
        ET.SubElement(ET.SubElement(top, "strip", size=size), "card", type=t)
    shown = [(f, k) for f, k, _ in sh.filters if k != "min"]
    if shown or sh.color:
        right = ET.SubElement(ET.SubElement(cards, "edge", name="right"), "strip", size="160")
        for f, k in shown:
            ET.SubElement(right, "card", {"column": f"[{sh.source.name}].{f.instance()}", "type": "filter",
                                          **({"mode": "dropdown"} if k == "member" else {})})
        if sh.color:
            ET.SubElement(right, "card", {"pane-specification-id": "0", "param": f"[{sh.source.name}].{sh.color.instance()}",
                                          "type": "color"})
    ET.SubElement(w, "simple-id", uuid=_uuid("win", sh.name))
    return w


def _dashboard(name: str, title: str, zones: list[tuple[str, int, int, int, int]],
               filters: list[tuple[Sheet, F, int, int, int, int, bool]]) -> tuple[ET.Element, ET.Element]:
    """zones: (sheet name, x, y, w, h) in 0-100000 units; filters: (sheet, field, x, y, w, h, single)."""
    d = ET.Element("dashboard", name=name)
    ET.SubElement(d, "style")
    ET.SubElement(d, "size", maxheight="900", maxwidth="1400", minheight="900", minwidth="1400", **{"sizing-mode": "fixed"})
    root = ET.SubElement(ET.SubElement(d, "zones"), "zone", h="100000", id="1", w="100000", x="0", y="0",
                         **{"type-v2": "layout-basic"})
    zid = 2
    t = ET.SubElement(root, "zone", h="6000", id=str(zid), w="98000", x="1000", y="1000", floating="true",
                      **{"type-v2": "text"})
    run = ET.SubElement(ET.SubElement(t, "formatted-text"), "run", {"fontsize": "18", "bold": "true"})
    run.text = title
    for sheet, x, y, w, h in zones:
        zid += 1
        ET.SubElement(root, "zone", h=str(h), id=str(zid), name=sheet, w=str(w), x=str(x), y=str(y), floating="true")
    for sh, f, x, y, w, h, single in filters:
        zid += 1
        ET.SubElement(root, "zone", {"h": str(h), "id": str(zid), "name": sh.name, "w": str(w), "x": str(x),
                                     "y": str(y), "floating": "true", "type-v2": "filter",
                                     "param": f"[{sh.source.name}].{f.instance()}",
                                     **({"mode": "dropdown"} if single else {"mode": "checkdropdown"})})
    ET.SubElement(d, "simple-id", uuid=_uuid("db", name))
    win = ET.Element("window", {"class": "dashboard", "maximized": "true", "name": name})
    vps = ET.SubElement(win, "viewpoints")
    for sheet, *_ in zones:
        ET.SubElement(vps, "viewpoint", name=sheet)
    ET.SubElement(win, "active", id="-1")
    ET.SubElement(win, "simple-id", uuid=_uuid("dbwin", name))
    return d, win


def build_workbook(directory: Path, path: Path) -> None:
    directory = directory.resolve()
    shots = Source("shots.csv", "Şutlar", dimensions=("match_id", "week", "plot_x", "plot_y", "xfg"), calcs=[
        Calc("hexx", "Hex X", "HEXBINX([plot_x] / 0.6, [plot_y] / 0.6) * 0.6", "real", "dimension", "quantitative"),
        Calc("hexy", "Hex Y", "HEXBINY([plot_x] / 0.6, [plot_y] / 0.6) * 0.6", "real", "dimension", "quantitative"),
        Calc("poe", "POE / şut", "(SUM([pts]) - SUM([xpts])) / COUNT([pts])", "real", "measure", "quantitative"),
        Calc("n", "Şut sayısı", "COUNT([pts])", "integer", "measure", "quantitative"),
        Calc("result", "Sonuç", 'IF [made] THEN "İsabet" ELSE "Kaçan" END', "string", "dimension", "nominal"),
    ])
    players = Source("player_shooting.csv", "Oyuncu şut", dimensions=("team_season_id", "player_id"))
    teams = Source("team_shooting.csv", "Takım şut", dimensions=("team_season_id",))
    pairs = Source("pairs.csv", "İkililer", dimensions=("team_season_id", "player_a_id", "player_b_id"))
    assists = Source("assist_pairs.csv", "Asist ağı", dimensions=("team_season_id", "passer_id", "scorer_id"))
    timeline = Source("score_timeline.csv", "Skor akışı",
                      dimensions=("match_id", "seq", "action_number", "period_ord", "team_season_id", "player_id",
                                  "week", "home_id", "away_id", "minute", "elapsed_s", "margin_home", "home_score",
                                  "away_score"),
                      calcs=[Calc("match", "Maç", 'STR([date]) + "  " + [home] + " - " + [away]', "string",
                                  "dimension", "nominal")])
    flow = Source("game_flow.csv", "Maç özeti", dimensions=("match_id", "team_season_id", "opponent_id", "week"),
                  calcs=[Calc("game", "Maç (takım)", 'STR([date]) + "  " + [team] + " - " + [opponent]', "string",
                              "dimension", "nominal")])
    clutch = Source("clutch_team.csv", "Clutch takım", dimensions=("team_season_id",))
    sources = [shots, players, teams, pairs, assists, timeline, flow, clutch]
    for s in sources:
        _read_columns(s, directory)
    first_match = pl.read_csv(directory / "score_timeline.csv", try_parse_dates=True).sort("date", "match_id").row(0, named=True)
    match_label = f"{first_match['date']}  {first_match['home']} - {first_match['away']}"
    team = pl.read_csv(directory / "pairs.csv").group_by("team").agg(pl.col("poss").max()).sort("poss", descending=True)["team"][0]

    court = [(F("plot_x", "none", "qk"), "cols", 0.0, 15.0), (F("plot_y", "none", "qk"), "rows", 0.0, 14.0)]
    sheets = [
        Sheet("Şut haritası", shots, cols=[F("plot_x", "none", "qk")], rows=[F("plot_y", "none", "qk")],
              mark="Circle", color=F("Calculation_result"), aggregate=False, axes=court,
              tooltip=[F("player"), F("zone"), F("xfg", "none", "qk"), F("period")],
              filters=[(F("team"), "all", None), (F("player"), "all", None), (F("zone"), "all", None),
                       (F("clutch"), "all", None), (F("transition"), "all", None)],
              title="Şut haritası (arka plan: court.png, X 0-15, Y 0-14)"),
        Sheet("Altıgen POE", shots, cols=[F("Calculation_hexx", "none", "qk")], rows=[F("Calculation_hexy", "none", "qk")],
              mark="Circle", color=F("Calculation_poe", "usr", "qk"), size=F("Calculation_n", "usr", "qk"),
              axes=[(F("Calculation_hexx", "none", "qk"), "cols", 0.0, 15.0), (F("Calculation_hexy", "none", "qk"), "rows", 0.0, 14.0)],
              filters=[(F("team"), "all", None), (F("player"), "all", None)],
              title="Beklenenin üstünde/altında: şut başına sayı − xPTS"),
        Sheet("Oyuncu kalite × başarı", players, cols=[F("xpts_per_shot", "sum", "qk")],
              rows=[F("poe_per_shot", "sum", "qk")], mark="Circle", color=F("team"), size=F("shots", "sum", "qk"),
              detail=[F("player")], tooltip=[F("poe_se", "sum", "qk"), F("pts", "sum", "qk"), F("xpts", "sum", "qk")],
              filters=[(F("shots", "sum", "qk"), "min", 60)],
              title="Şut kalitesi (x) ve beklenenin üstünde atma (y), en az 60 şut"),
        Sheet("Takım şut kalitesi", teams, rows=[F("team"), F("side")], cols=[F("xpts_per_shot", "sum", "qk")],
              mark="Bar", color=F("side"), label=[F("xpts_per_shot", "sum", "qk")],
              title="Takımın aldığı (offense) ve rakibe verdiği (defense) şut kalitesi"),
        Sheet("İkili net", pairs, rows=[F("player_a")], cols=[F("player_b")], mark="Square",
              color=F("net", "sum", "qk"), label=[F("poss", "sum", "qk")],
              tooltip=[F("minutes", "sum", "qk"), F("a_without_b_net", "sum", "qk"), F("b_without_a_net", "sum", "qk")],
              filters=[(F("team"), "member", team), (F("poss", "sum", "qk"), "min", 100)],
              title="İkili birlikteyken net reyting (etiket: hücum sayısı, en az 100)"),
        Sheet("Asist ağı", assists, rows=[F("passer")], cols=[F("scorer")], mark="Square",
              size=F("assists", "sum", "qk"), color=F("pts", "sum", "qk"), label=[F("assists", "sum", "qk")],
              filters=[(F("team"), "member", team)], title="Kim kime asist yapıyor"),
        Sheet("Maç akışı", timeline, cols=[F("minute", "none", "qk")], rows=[F("margin_home", "none", "qk")],
              mark="Line", aggregate=False, tooltip=[F("player"), F("home_score", "none", "qk"), F("away_score", "none", "qk")],
              filters=[(F("Calculation_match"), "member", match_label)],
              title="Skor farkı (ev sahibi − deplasman)"),
        Sheet("Geri dönüşler", flow, rows=[F("Calculation_game")], cols=[F("largest_deficit", "sum", "qk")], mark="Bar",
              label=[F("largest_deficit", "sum", "qk")], filters=[(F("won"), "member", "true"),
                                                                  (F("largest_deficit", "sum", "qk"), "min", 8)],
              title="Kazanan takımın kapattığı en büyük fark (8+)"),
        Sheet("Clutch", clutch, rows=[F("team")], cols=[F("net", "sum", "qk")], mark="Bar",
              label=[F("poss", "sum", "qk")], title="Son 5 dk, fark ≤ 5: net reyting (etiket: hücum)"),
    ]
    by = {s.name: s for s in sheets}
    dashboards = [
        _dashboard("Şut Kalitesi", "TBL 2026-27 · Şut kalitesi (xPTS)",
                   [("Şut haritası", 1000, 8000, 45000, 84000), ("Oyuncu kalite × başarı", 47000, 8000, 52000, 46000),
                    ("Takım şut kalitesi", 47000, 55000, 52000, 44000)],
                   [(by["Şut haritası"], F("team"), 1000, 92500, 22000, 6500, False),
                    (by["Şut haritası"], F("player"), 23500, 92500, 22000, 6500, False)]),
        _dashboard("İkililer", "TBL 2026-27 · İkililer ve asist ağı",
                   [("İkili net", 1000, 8000, 55000, 91000), ("Asist ağı", 57000, 8000, 42000, 91000)], []),
        _dashboard("Maç Akışı", "TBL 2026-27 · Maç akışı, geri dönüşler, clutch",
                   [("Maç akışı", 1000, 8000, 98000, 44000), ("Geri dönüşler", 1000, 53000, 55000, 46000),
                    ("Clutch", 57000, 53000, 42000, 46000)], []),
    ]

    wb = ET.Element("workbook", {"original-version": VERSION, "source-build": BUILD, "source-platform": "mac",
                                 "version": VERSION})
    manifest = ET.SubElement(wb, "document-format-change-manifest")
    for tag in ("AnimationOnByDefault", "IntuitiveSorting", "IntuitiveSorting_SP2", "MarkAnimation",
                "ObjectModelEncapsulateLegacy", "ObjectModelTableType", "SchemaViewerObjectModel",
                "SheetIdentifierTracking", "WindowsPersistSimpleIdentifiers"):
        ET.SubElement(manifest, tag)
    prefs = ET.SubElement(wb, "preferences")
    ET.SubElement(prefs, "preference", name="ui.encoding.shelf.height", value="24")
    ET.SubElement(prefs, "preference", name="ui.shelf.height", value="26")
    dss = ET.SubElement(wb, "datasources")
    for s in sources:
        dss.append(_datasource(s, directory))
    wss = ET.SubElement(wb, "worksheets")
    for sh in sheets:
        wss.append(_worksheet(sh))
    dbs = ET.SubElement(wb, "dashboards")
    wins = ET.SubElement(wb, "windows", {"source-height": "30"})
    for d, w in dashboards:
        dbs.append(d)
    for sh in sheets:
        wins.append(_window_ws(sh))
    for _, w in dashboards:
        wins.append(w)
    tree = ET.ElementTree(wb)
    ET.indent(tree, "  ")
    with open(path, "wb") as fh:
        fh.write(b"<?xml version='1.0' encoding='utf-8' ?>\n\n")
        tree.write(fh, encoding="utf-8", xml_declaration=False)
