"""Tableau workbook (.twb) writer: parameters, data sources over CSV + Hyper extracts,
worksheets, dashboards and parameter actions.

Tableau has no API for building views, so the XML is written here, following what Tableau
saves (document version 18.1; patterns taken from workbooks saved by Tableau 2024.3-2026.3).
Tableau validates the document on open, so only elements and attributes seen in real
workbooks are used. Tableau Public only saves workbooks whose data sources are extracts,
so every CSV also gets a Hyper extract (`<name>.hyper`). What the dashboards contain is in
dashboards.py.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

USER_NS = "http://www.tableausoftware.com/xml/user"
ET.register_namespace("user", USER_NS)
U = "{%s}" % USER_NS
VERSION = "18.1"
BUILD = "2026.3.0 (20263.26.1015.1742)"
DERIV = {"none": "None", "sum": "Sum", "avg": "Avg", "min": "Min", "max": "Max", "cnt": "Count", "usr": "User",
         "attr": "Attribute"}
KIND = {"nk": "nominal", "ok": "ordinal", "qk": "quantitative"}


def _id(*parts: str, n: int = 28) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:n]


def _uuid(*parts: str) -> str:
    return "{" + str(uuid.UUID(hashlib.md5("|".join(parts).encode()).hexdigest())).upper() + "}"


def _q(v) -> str:
    """A literal as Tableau writes it in value/member attributes and formulas."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return '"' + str(v).replace('"', '""') + '"'


# -- model ------------------------------------------------------------------------------
@dataclass
class Param:
    caption: str
    datatype: str  # string / integer / real
    default: object
    members: list[tuple[object, str | None]] = field(default_factory=list)  # (value, alias) for a list
    range: tuple[float, float, float] | None = None  # (min, max, step) for a range
    name: str = ""  # set by Workbook: Parameter N

    @property
    def ref(self) -> str:
        return f"[Parameters].[{self.name}]"


@dataclass
class Calc:
    key: str
    caption: str
    formula: str  # may use {Takım}-style placeholders for parameter captions
    datatype: str
    role: str = "dimension"  # dimension / measure
    type: str = "nominal"  # nominal / ordinal / quantitative

    @property
    def name(self) -> str:
        return f"Calculation_{self.key}"


@dataclass
class F:
    """A field on a shelf: column or calc name, derivation, n(ominal)/o(rdinal)/q(uantitative)."""
    col: str
    deriv: str = "none"
    kind: str = "nk"

    @property
    def inst(self) -> str:
        return f"[{self.deriv}:{self.col}:{self.kind}]"


@dataclass
class Source:
    file: str
    caption: str
    dimensions: tuple[str, ...] = ()  # numeric columns used as dimensions
    calcs: list[Calc] = field(default_factory=list)
    captions: dict[str, str] = field(default_factory=dict)
    # colour per value of a field: a row-level field as "[none:name:nk]", an aggregate calc by its bare name
    colors: dict[str, dict[str, str]] = field(default_factory=dict)
    image: tuple[str, str, str, tuple[float, float, float, float]] | None = None  # (file, x, y, (left, right, bottom, top))
    columns: dict[str, str] = field(default_factory=dict)  # filled from the CSV: name -> datatype

    @property
    def name(self) -> str:
        return "federated." + _id("ds", self.file)

    def calc(self, name: str) -> Calc | None:
        return next((c for c in self.calcs if c.name == name), None)

    def kind(self, col: str) -> tuple[str, str, str]:
        c = self.calc(col)
        if c:
            return c.datatype, c.role, c.type
        dt = self.columns[col]
        if dt in ("string", "boolean"):
            return dt, "dimension", "nominal"
        if dt == "date":
            return dt, "dimension", "ordinal"
        if col in self.dimensions:
            return dt, "dimension", "quantitative" if dt == "real" else "ordinal"
        return dt, "measure", "quantitative"


@dataclass
class Axis:
    field: F
    scope: str  # rows / cols
    min: float | None = None
    max: float | None = None
    reverse: bool = False
    title: str | None = None
    hidden: bool = False
    fmt: str | None = None  # number format of the tick labels


@dataclass
class RefLine:
    field: F
    value: float
    label: str = ""


@dataclass
class Sheet:
    name: str
    source: Source
    rows: list[F] = field(default_factory=list)
    cols: list[F] = field(default_factory=list)
    mark: str = "Automatic"
    color: F | None = None
    color_range: tuple[float, float] | None = None  # continuous colour: red-blue diverging centred on 0
    color_seq: str | None = None  # continuous colour: a sequential built-in palette, e.g. blue_10_0
    line_step: bool = False
    label_cull: bool = True
    size: F | None = None
    shape: F | None = None
    label: list[F] = field(default_factory=list)
    detail: list[F] = field(default_factory=list)
    tooltip: list[F] = field(default_factory=list)
    path: F | None = None
    aggregate: bool = True
    filters: list[tuple] = field(default_factory=list)  # ("true", F) / ("min", F, v) / ("member", F, v)
    sorts: list[tuple[F, F, str, str]] = field(default_factory=list)  # (dimension, by, ASC/DESC, rows/columns)
    axes: list[Axis] = field(default_factory=list)
    reflines: list[RefLine] = field(default_factory=list)
    formats: dict[str, str] = field(default_factory=dict)  # F.inst -> Tableau number format
    title: list[tuple[str, dict]] | None = None  # runs: text, "<[usr:x:nk]>" field or "<Parameters.Takım>"
    tooltip_runs: list[tuple[str, dict]] | None = None
    mark_size: float | None = None
    hide_headers: list[F] = field(default_factory=list)


@dataclass
class Zone:
    kind: str  # sheet / text / param / legend / nav / blank
    x: int
    y: int
    w: int
    h: int
    sheet: str | None = None
    runs: list[tuple[str, dict]] | None = None
    param: Param | None = None
    mode: str = "compact"
    target: str | None = None  # nav: dashboard name
    legend_field: F | None = None
    show_title: bool = True
    fit: str = "entire-view"  # entire-view / fit-width / fit-height / standard
    map_controls: bool = True  # zoom/pan toolbar on views with a background image
    background: str | None = None
    title: str | None = None


@dataclass
class Dashboard:
    name: str
    zones: list[Zone]
    width: int = 1200
    height: int = 800


@dataclass
class ParamAction:
    caption: str
    dashboard: str
    sheet: str
    source: F  # a dimension on the sheet (on Detail)
    param: Param


# -- writer -----------------------------------------------------------------------------
DATATYPES = {pl.Int8: "integer", pl.Int16: "integer", pl.Int32: "integer", pl.Int64: "integer", pl.UInt32: "integer",
             pl.UInt64: "integer", pl.Float32: "real", pl.Float64: "real", pl.Boolean: "boolean", pl.Date: "date",
             pl.String: "string"}
HYPER_TYPES = {"string": ("TEXT", "129"), "integer": ("BIG_INT", "20"), "real": ("DOUBLE", "5"),
               "boolean": ("BOOL", "11"), "date": ("DATE", "133")}


class Workbook:
    def __init__(self, directory: Path, params: list[Param], sources: list[Source], sheets: list[Sheet],
                 dashboards: list[Dashboard], actions: list[ParamAction], locale: str = "tr_TR",
                 start: str | None = None):
        self.dir = directory.resolve()
        self.params, self.sources, self.sheets = params, sources, sheets
        self.dashboards, self.actions, self.locale, self.start = dashboards, actions, locale, start
        for i, p in enumerate(params, 1):
            p.name = f"Parameter {i}"
        self.by_caption = {p.caption: p for p in params}
        for s in sources:
            df = pl.read_csv(self.dir / s.file, n_rows=5000, try_parse_dates=True, infer_schema_length=5000)
            s.columns = {c: DATATYPES.get(df.schema[c].base_type(), "string") for c in df.columns}
            for c in s.calcs:
                c.formula = self.resolve(c.formula)

    def resolve(self, formula: str) -> str:
        """{Takım} -> [Parameters].[Parameter 1]."""
        return re.sub(r"\{([^{}]+)\}", lambda m: self.by_caption[m.group(1)].ref if m.group(1) in self.by_caption
                      else m.group(0), formula)

    # parameters and data sources
    def _param_columns(self, parent: ET.Element, only: set[str] | None = None) -> None:
        for p in self.params:
            if only is not None and p.name not in only:
                continue
            typ = "quantitative" if p.datatype in ("integer", "real") else "nominal"
            domain = "range" if p.range else "list" if p.members else "any"
            current = next((a for v, a in p.members if v == p.default and a), None)  # shown in the control
            el = ET.SubElement(parent, "column", {**({"alias": current} if current else {}), "caption": p.caption,
                                                  "datatype": p.datatype, "name": f"[{p.name}]",
                                                  "param-domain-type": domain, "role": "measure", "type": typ,
                                                  "value": _q(p.default)})
            ET.SubElement(el, "calculation", {"class": "tableau", "formula": _q(p.default)})
            if p.range:
                ET.SubElement(el, "range", granularity=repr(p.range[2]), max=repr(p.range[1]), min=repr(p.range[0]))
            elif p.members:
                if any(a for _, a in p.members):
                    al = ET.SubElement(el, "aliases")
                    for v, a in p.members:
                        if a:
                            ET.SubElement(al, "alias", key=_q(v), value=a)
                ms = ET.SubElement(el, "members")
                for v, a in p.members:
                    ET.SubElement(ms, "member", {**({"alias": a} if a else {}), "value": _q(v)})

    def _write_extract(self, s: Source) -> Path:
        from tableauhyperapi import (Connection, CreateMode, HyperProcess, Inserter, SqlType, TableDefinition,
                                     TableName, Telemetry)

        path = self.dir / s.file.replace(".csv", ".hyper")
        df = pl.read_csv(self.dir / s.file, try_parse_dates=True, infer_schema_length=None).select(list(s.columns))
        table = TableDefinition(TableName("Extract", "Extract"), [
            TableDefinition.Column(c, getattr(SqlType, HYPER_TYPES[dt][0].lower())()) for c, dt in s.columns.items()])
        with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU, parameters={"log_dir": str(self.dir)}) as hyper, \
                Connection(hyper.endpoint, path, CreateMode.CREATE_AND_REPLACE) as conn:
            conn.catalog.create_schema("Extract")
            conn.catalog.create_table(table)
            with Inserter(conn, table) as ins:
                ins.add_rows(df.iter_rows())
                ins.execute()
        return path

    def _datasource(self, s: Source) -> ET.Element:
        table = s.file.replace(".csv", "#csv")
        conn_name = "textscan." + _id("conn", s.file)
        ds = ET.Element("datasource", caption=s.caption, inline="true", name=s.name, version=VERSION)
        fed = ET.SubElement(ds, "connection", {"class": "federated"})
        named = ET.SubElement(ET.SubElement(fed, "named-connections"), "named-connection", caption=s.caption,
                              name=conn_name)
        ET.SubElement(named, "connection", {"class": "textscan", "directory": str(self.dir), "filename": s.file,
                                            "server": "", "workgroup-auth-mode": "as-is"})

        def relation(parent, with_conn=True):
            attrs = {"name": s.file, "table": f"[{table}]", "type": "table"}
            rel = ET.SubElement(parent, "relation", {"connection": conn_name, **attrs} if with_conn else attrs)
            cols = ET.SubElement(rel, "columns", {"character-set": "UTF-8", "header": "yes", "locale": "en",
                                                  "separator": ","})
            for i, (c, dt) in enumerate(s.columns.items()):
                ET.SubElement(cols, "column", datatype=dt, name=c, ordinal=str(i))

        relation(fed)
        ET.SubElement(ds, "aliases", enabled="yes")
        for c in s.calcs:
            el = ET.SubElement(ds, "column", caption=c.caption, datatype=c.datatype, name=f"[{c.name}]", role=c.role,
                               type=c.type)
            ET.SubElement(el, "calculation", {"class": "tableau", "formula": c.formula})
        for c in s.columns:
            dt, role, typ = s.kind(c)
            ET.SubElement(ds, "column", {**({"caption": s.captions[c]} if c in s.captions else {}), "datatype": dt,
                                         "name": f"[{c}]", "role": role, "type": typ})
        for key in s.colors:  # a [none:x:nk] colour key needs its column instance declared here
            if key.startswith("[none:"):
                col = key[6:-4]
                ET.SubElement(ds, "column-instance", column=f"[{col}]", derivation="None", name=key, pivot="key",
                              type="nominal")
        if s.image:
            img, xc, yc, (left, right, bottom, top) = s.image
            mi = ET.SubElement(ET.SubElement(ds, "mapped-images"), "mapped-image", caption=Path(img).stem,
                               expression=(self.dir / img).as_uri(), name="[MappedImage1]")
            ET.SubElement(mi, "mapping", bottom=repr(bottom), left=repr(left), right=repr(right), top=repr(top),
                          x=f"[{xc}]", y=f"[{yc}]")
            ET.SubElement(mi, "image-options", **{"show-entire-image": "true"})
        ds.append(self._extract(s, self._write_extract(s)))
        ET.SubElement(ds, "layout", {"dim-ordering": "alphabetic", "measure-ordering": "alphabetic",
                                     "show-structure": "true"})
        if s.colors:
            rule = ET.SubElement(ET.SubElement(ds, "style"), "style-rule", element="mark")
            for fld, mapping in s.colors.items():
                enc = ET.SubElement(rule, "encoding", attr="color", type="palette",
                                    field=fld if fld.startswith("[") else f"[{fld}]")
                for v, hexv in mapping.items():
                    ET.SubElement(ET.SubElement(enc, "map", to=hexv), "bucket").text = _q(v)
        obj = ET.SubElement(ET.SubElement(ET.SubElement(ds, "object-graph"), "objects"), "object",
                            caption=s.file, id=f"{s.file}_{_id('obj', s.file, n=32).upper()}")
        relation(ET.SubElement(obj, "properties", context=""), with_conn=True)
        ET.SubElement(ET.SubElement(obj, "properties", context="extract"), "relation", name="Extract",
                      table="[Extract].[Extract]", type="table")
        return ds

    def _extract(self, s: Source, hyper: Path) -> ET.Element:
        # object-id is required once the ObjectModelExtractV2 format flag is set
        ex = ET.Element("extract", count="-1", enabled="true", units="records", **{"object-id": ""})
        conn = ET.SubElement(ex, "connection", {
            "access_mode": "readonly", "author-locale": "en_US", "class": "hyper", "dbname": str(hyper),
            "default-settings": "hyper", "schema": "Extract", "sslmode": "", "tablename": "Extract",
            "update-time": time.strftime("%m/%d/%Y %I:%M:%S %p"), "username": "tableau_internal_user"})
        ET.SubElement(conn, "relation", name="Extract", table="[Extract].[Extract]", type="table")
        recs = ET.SubElement(conn, "metadata-records")
        for i, (c, dt) in enumerate(s.columns.items()):
            r = ET.SubElement(recs, "metadata-record", {"class": "column"})
            for tag, text in (("remote-name", c), ("remote-type", HYPER_TYPES[dt][1]), ("local-name", f"[{c}]"),
                              ("parent-name", "[Extract]"), ("remote-alias", c), ("ordinal", str(i)),
                              ("family", s.file), ("local-type", dt),
                              ("aggregation", "Sum" if dt in ("integer", "real") else "Count"),
                              ("contains-null", "true")):
                ET.SubElement(r, tag).text = text
        return ex

    # worksheets
    def _ref(self, s: Source, f: F) -> str:
        return f"[{s.name}].{f.inst}"

    def _runs(self, parent: ET.Element, runs: list[tuple[str, dict]], src: Source | None) -> None:
        ft = ET.SubElement(parent, "formatted-text")
        for text, style in runs:
            if text.startswith("<Parameters."):
                text = f"<{self.by_caption[text[12:-1]].ref}>"
            elif text.startswith("<[") and src is not None:
                text = f"<[{src.name}].{text[1:-1]}>"
            ET.SubElement(ft, "run", {k: str(v) for k, v in style.items()}).text = text

    def _used_calcs(self, s: Source, names: set[str]) -> list[Calc]:
        """Calcs used on the sheet plus the calcs they reference, transitively."""
        out, todo = [], list(names)
        while todo:
            c = s.calc(todo.pop())
            if c and c not in out:
                out.append(c)
                todo += re.findall(r"\[(Calculation_[^\]]+)\]", c.formula)
        return out

    def _worksheet(self, sh: Sheet) -> ET.Element:
        s = sh.source
        # Tableau only shows a dimension in a tooltip as ATTR(): convert, and point the tooltip text at it
        conv = {f.inst: F(f.col, "attr", f.kind) for f in sh.tooltip if f.deriv == "none" and f.kind in ("nk", "ok")}
        if conv:
            sh.tooltip = [conv.get(f.inst, f) for f in sh.tooltip]
            sh.tooltip_runs = [(f"<{conv[t[1:-1]].inst}>" if t.startswith("<[") and t[1:-1] in conv else t, st)
                               for t, st in (sh.tooltip_runs or [])]
        ws = ET.Element("worksheet", name=sh.name)
        if sh.title:
            self._runs(ET.SubElement(ET.SubElement(ws, "layout-options"), "title"), sh.title, s)
        table = ET.SubElement(ws, "table")
        view = ET.SubElement(table, "view")
        dss = ET.SubElement(view, "datasources")
        used = [*sh.rows, *sh.cols, *(x for x in (sh.color, sh.size, sh.shape, sh.path) if x), *sh.label, *sh.detail,
                *sh.tooltip, *(f[1] for f in sh.filters), *(x for st in sh.sorts for x in st[:2])]
        pnames = set()
        for runs in (sh.title or [], sh.tooltip_runs or []):
            for text, _ in runs:
                m = re.fullmatch(r"<\[(\w+):([^:\]]+):(\w+)\]>", text)
                if m:
                    used.append(F(m.group(2), m.group(1), m.group(3)))
                if text.startswith("<Parameters."):
                    pnames.add(self.by_caption[text[12:-1]].name)
        calcs = self._used_calcs(s, {f.col for f in used})
        pnames |= {m for c in calcs for m in re.findall(r"\[Parameters\]\.\[([^\]]+)\]", c.formula)}
        ET.SubElement(dss, "datasource", caption=s.caption, name=s.name)  # the sheet's own source first
        if pnames:
            ET.SubElement(dss, "datasource", name="Parameters")
        if pnames:
            self._param_columns(ET.SubElement(view, "datasource-dependencies", datasource="Parameters"), pnames)
        deps = ET.SubElement(view, "datasource-dependencies", datasource=s.name)
        base = {f.col for f in used if not s.calc(f.col)} | {
            b for c in calcs for b in re.findall(r"(?<!\]\.)\[([^\[\]]+)\]", c.formula) if b in s.columns}
        for c in calcs:
            el = ET.SubElement(deps, "column", caption=c.caption, datatype=c.datatype, name=f"[{c.name}]",
                               role=c.role, type=c.type)
            ET.SubElement(el, "calculation", {"class": "tableau", "formula": c.formula})
        for col in sorted(base):
            dt, role, typ = s.kind(col)
            ET.SubElement(deps, "column", {**({"caption": s.captions[col]} if col in s.captions else {}),
                                           "datatype": dt, "name": f"[{col}]", "role": role, "type": typ})
        seen = set()
        for f in used:
            if f.inst not in seen:
                seen.add(f.inst)
                ET.SubElement(deps, "column-instance", column=f"[{f.col}]", derivation=DERIV[f.deriv], name=f.inst,
                              pivot="key", type=KIND[f.kind])
        for flt in sh.filters:
            kind, f = flt[0], flt[1]
            if kind == "min":
                el = ET.SubElement(view, "filter", {"class": "quantitative", "column": self._ref(s, f),
                                                    "included-values": "in-range"})
                ET.SubElement(el, "min").text = repr(flt[2])
            else:
                value = "true" if kind == "true" else _q(flt[2])
                el = ET.SubElement(view, "filter", {"class": "categorical", "column": self._ref(s, f)})
                ET.SubElement(el, "groupfilter", {"function": "member", "level": f.inst, "member": value,
                                                  U + "ui-domain": "database", U + "ui-enumeration": "inclusive",
                                                  U + "ui-marker": "enumerate"})
        if sh.sorts:
            ss = ET.SubElement(view, "shelf-sorts")
            for dim, by, direction, shelf in sh.sorts:
                ET.SubElement(ss, "shelf-sort-v2", {"dimension-to-sort": self._ref(s, dim), "direction": direction,
                                                    "is-on-innermost-dimension": "true",
                                                    "measure-to-sort-by": self._ref(s, by), "shelf": shelf})
        ET.SubElement(view, "aggregation", value="true" if sh.aggregate else "false")

        style = ET.SubElement(table, "style")
        if sh.axes:
            rule = ET.SubElement(style, "style-rule", element="axis")
            for a in sh.axes:
                attrs = {"attr": "space", "class": "0", "field": self._ref(s, a.field), "field-type": "quantitative"}
                if a.min is not None:
                    attrs.update({"max": repr(a.max), "min": repr(a.min), "range-type": "fixed"})
                if a.reverse:
                    attrs["reverse"] = "true"
                ET.SubElement(rule, "encoding", {**attrs, "scope": a.scope, "type": "space"})
            for a in sh.axes:
                if a.title is not None:
                    ET.SubElement(rule, "format", {"attr": "title", "class": "0", "field": self._ref(s, a.field),
                                                   "scope": a.scope, "value": a.title})
                if a.hidden:
                    ET.SubElement(rule, "format", {"attr": "display", "class": "0", "field": self._ref(s, a.field),
                                                   "scope": a.scope, "value": "false"})
                if a.fmt:
                    ET.SubElement(rule, "format", {"attr": "text-format", "field": self._ref(s, a.field), "value": a.fmt})
        rule = ET.SubElement(style, "style-rule", element="worksheet")
        ET.SubElement(rule, "format", attr="display-field-labels", scope="rows", value="false")
        ET.SubElement(rule, "format", attr="display-field-labels", scope="cols", value="false")
        if sh.hide_headers:
            rule = ET.SubElement(style, "style-rule", element="header")
            for f in sh.hide_headers:
                ET.SubElement(rule, "format", {"attr": "display", "field": self._ref(s, f), "value": "false"})
        if sh.formats:
            for element in ("cell", "label", "tooltip"):
                rule = ET.SubElement(style, "style-rule", element=element)
                for inst, fmt in sh.formats.items():
                    ET.SubElement(rule, "format", {"attr": "text-format", "field": f"[{s.name}].{inst}", "value": fmt})
        if sh.color is not None and sh.color_range is not None:
            lo, hi = sh.color_range
            ET.SubElement(ET.SubElement(style, "style-rule", element="mark"), "encoding", {
                "attr": "color", "center": "0.0", "field": self._ref(s, sh.color), "max": repr(hi), "min": repr(lo),
                "palette": "red_blue_diverging_10_0", "type": "interpolated"})
        elif sh.color is not None and sh.color_seq:
            ET.SubElement(ET.SubElement(style, "style-rule", element="mark"), "encoding", {
                "attr": "color", "field": self._ref(s, sh.color), "palette": sh.color_seq, "type": "interpolated"})

        pane = ET.SubElement(ET.SubElement(table, "panes"), "pane",
                             {"selection-relaxation-option": "selection-relaxation-allow"})
        ET.SubElement(ET.SubElement(pane, "view"), "breakdown", value="auto")
        ET.SubElement(pane, "mark", {"class": sh.mark})
        enc = ET.SubElement(pane, "encodings")
        for tag, f in (("color", sh.color), ("size", sh.size), ("shape", sh.shape), ("path", sh.path)):
            if f is not None:
                ET.SubElement(enc, tag, column=self._ref(s, f))
        for tag, fs in (("text", sh.label), ("lod", sh.detail), ("tooltip", sh.tooltip)):
            for f in fs:
                ET.SubElement(enc, tag, column=self._ref(s, f))
        for i, r in enumerate(sh.reflines):
            ET.SubElement(pane, "reference-line", {
                "axis-column": self._ref(s, r.field), "enable-instant-analytics": "true", "formula": "constant",
                "id": f"refline{i}", "label": r.label, "label-type": "custom" if r.label else "none",
                "scope": "per-pane", "value": repr(r.value), "value-column": self._ref(s, r.field),
                "z-order": str(i + 1)})
        if sh.tooltip_runs:
            self._runs(ET.SubElement(pane, "customized-tooltip", {"show-buttons": "false"}), sh.tooltip_runs, s)
        mrule = ET.SubElement(ET.SubElement(pane, "style"), "style-rule", element="mark")
        if sh.label:
            ET.SubElement(mrule, "format", attr="mark-labels-show", value="true")
            ET.SubElement(mrule, "format", attr="mark-labels-cull", value="true" if sh.label_cull else "false")
        if sh.line_step:
            ET.SubElement(mrule, "format", attr="line-interpolation", value="step")
        if sh.mark_size is not None:
            ET.SubElement(mrule, "format", attr="size", value=repr(sh.mark_size))

        def shelf(fs: list[F]) -> str | None:
            if not fs:
                return None
            refs = " / ".join(self._ref(s, f) for f in fs)
            return refs if len(fs) == 1 else f"({refs})"

        ET.SubElement(table, "rows").text = shelf(sh.rows)
        ET.SubElement(table, "cols").text = shelf(sh.cols)
        ET.SubElement(ws, "simple-id", uuid=_uuid("ws", sh.name))
        return ws

    # dashboards
    def _dashboard(self, d: Dashboard) -> ET.Element:
        el = ET.Element("dashboard", name=d.name)
        dstyle = ET.SubElement(el, "style")
        texts = [i for i, z in enumerate(d.zones, 2) if z.kind == "text"]
        if texts:  # text objects start at the top of their box
            rule = ET.SubElement(dstyle, "style-rule", element="dash-text")
            for i in texts:
                ET.SubElement(rule, "format", attr="vertical-align", id=f"dash-text_{i}", value="top")
        ET.SubElement(el, "size", maxheight=str(d.height), maxwidth=str(d.width), minheight=str(d.height),
                      minwidth=str(d.width), **{"sizing-mode": "fixed"})
        controls = {z.param.name for z in d.zones if z.kind == "param"}
        if controls:  # a dashboard with parameter controls lists the parameters it shows
            ET.SubElement(ET.SubElement(el, "datasources"), "datasource", name="Parameters")
            self._param_columns(ET.SubElement(el, "datasource-dependencies", datasource="Parameters"), controls)
        # a tiled root, then every object floating: floating zones are further children of <zones>
        root = ET.SubElement(el, "zones")
        ET.SubElement(root, "zone", h="100000", id="1", w="100000", x="0", y="0", **{"type-v2": "layout-basic"})
        sx, sy = 100000 / d.width, 100000 / d.height
        sheets = {s.name: s for s in self.sheets}
        for i, z in enumerate(d.zones, 2):
            geo = {"h": str(round(z.h * sy)), "id": str(i), "w": str(round(z.w * sx)), "x": str(round(z.x * sx)),
                   "y": str(round(z.y * sy))}
            if z.kind == "sheet":
                zone = ET.SubElement(root, "zone", {**geo, "name": z.sheet,
                                                    **({} if z.show_title else {"show-title": "false"})})
            elif z.kind == "text":
                zone = ET.SubElement(root, "zone", {**geo, "type-v2": "text"})
                self._runs(zone, z.runs or [], None)
            elif z.kind == "param":
                zone = ET.SubElement(root, "zone", {**geo, "mode": z.mode, "param": z.param.ref, "type-v2": "paramctrl"})
            elif z.kind == "legend":
                src = sheets[z.sheet].source
                zone = ET.SubElement(root, "zone", {**geo, "name": z.sheet, "pane-specification-id": "0",
                                                    "param": self._ref(src, z.legend_field), "type-v2": "color"})
            elif z.kind == "nav":
                zone = ET.SubElement(root, "zone", {**geo, "type-v2": "dashboard-object"})
                current = z.target == d.name
                btn = ET.SubElement(zone, "button", {
                    "action": f'tabdoc:goto-sheet window-id="{_uuid("dbwin", z.target)}"', "button-type": "text"})
                vs = ET.SubElement(btn, "button-visual-state")
                ET.SubElement(vs, "caption").text = z.title or z.target
                ET.SubElement(vs, "button-caption-font-style", fontcolor="#ffffff" if current else "#1b2430",
                              fontname="Tableau Medium", fontsize="9")
                ET.SubElement(vs, "format", attr="background-color", value="#1b2430" if current else "#eef1f4")
            else:  # blank
                zone = ET.SubElement(root, "zone", {**geo, "type-v2": "empty"})
            zs = ET.SubElement(zone, "zone-style")
            ET.SubElement(zs, "format", attr="border-color", value="#000000")
            ET.SubElement(zs, "format", attr="border-style", value="none")
            ET.SubElement(zs, "format", attr="border-width", value="0")
            ET.SubElement(zs, "format", attr="margin", value="4" if z.kind in ("sheet", "param", "legend") else "0")
            if z.background:
                ET.SubElement(zs, "format", attr="background-color", value=z.background)
        ET.SubElement(el, "simple-id", uuid=_uuid("db", d.name))
        return el

    def _actions(self) -> ET.Element:
        acts = ET.Element("actions")
        for a in self.actions:
            sheet = next(s for s in self.sheets if s.name == a.sheet)
            el = ET.SubElement(acts, "edit-parameter-action", caption=a.caption,
                               name=f"[Action_{_id('act', a.caption, a.dashboard, a.sheet, a.param.caption, n=32).upper()}]")
            ET.SubElement(el, "activation", type="on-select")
            ET.SubElement(el, "source", dashboard=a.dashboard, type="sheet", worksheet=a.sheet)
            ET.SubElement(el, "agg-type", type="attr")
            ET.SubElement(el, "clear-option", type="do-nothing", value="s:LROOT:")
            ps = ET.SubElement(el, "params")
            ET.SubElement(ps, "param", name="source-field", value=self._ref(sheet.source, a.source))
            ET.SubElement(ps, "param", name="target-parameter", value=a.param.ref)
        return acts

    def _windows(self) -> ET.Element:
        wins = ET.Element("windows", {"source-height": "30"})
        for sh in self.sheets:
            w = ET.SubElement(wins, "window", {"class": "worksheet", "hidden": "true", "name": sh.name})
            cards = ET.SubElement(w, "cards")
            left = ET.SubElement(ET.SubElement(cards, "edge", name="left"), "strip", size="160")
            for t in ("pages", "filters", "marks"):
                ET.SubElement(left, "card", type=t)
            top = ET.SubElement(cards, "edge", name="top")
            for t, size in (("columns", "2147483647"), ("rows", "2147483647"), ("title", "31")):
                ET.SubElement(ET.SubElement(top, "strip", size=size), "card", type=t)
            ET.SubElement(w, "simple-id", uuid=_uuid("win", sh.name))
        for d in self.dashboards:
            w = ET.SubElement(wins, "window", {"class": "dashboard",
                                               **({"maximized": "true"} if d.name == self.start else {}), "name": d.name})
            vps = ET.SubElement(w, "viewpoints")
            for z in d.zones:
                if z.kind == "sheet":
                    vp = ET.SubElement(vps, "viewpoint", name=z.sheet)
                    if z.fit != "standard":
                        ET.SubElement(vp, "zoom", type=z.fit)
                    if not z.map_controls:
                        ET.SubElement(vp, "geo-search-visibility", value="0")
                        ET.SubElement(vp, "map-navigation", value="0")
            ET.SubElement(w, "active", id="-1")
            ET.SubElement(w, "simple-id", uuid=_uuid("dbwin", d.name))
        return wins

    def write(self, path: Path) -> None:
        wb = ET.Element("workbook", {"locale": self.locale, "original-version": VERSION, "source-build": BUILD,
                                     "source-platform": "mac", "version": VERSION})
        manifest = ET.SubElement(wb, "document-format-change-manifest")
        for tag in ("AnimationOnByDefault", "BasicButtonObject", "BasicButtonObjectTextSupport", "IntuitiveSorting", "IntuitiveSorting_SP2", "MarkAnimation", "NavigationAction",
                    "ObjectModelEncapsulateLegacy", "ObjectModelExtractV2", "ObjectModelTableType",
                    "ParameterAction", "ParameterActionClearSelection", "SchemaViewerObjectModel", "SheetIdentifierTracking",
                    "WindowsPersistSimpleIdentifiers"):
            ET.SubElement(manifest, tag)
        prefs = ET.SubElement(wb, "preferences")
        ET.SubElement(prefs, "preference", name="ui.encoding.shelf.height", value="24")
        ET.SubElement(prefs, "preference", name="ui.shelf.height", value="26")
        rule = ET.SubElement(ET.SubElement(wb, "style"), "style-rule", element="all")
        ET.SubElement(rule, "format", attr="font-family", value="Tableau Book")
        dss = ET.SubElement(wb, "datasources")
        if self.params:
            pds = ET.SubElement(dss, "datasource", hasconnection="false", inline="true", name="Parameters",
                                version=VERSION)
            ET.SubElement(pds, "aliases", enabled="yes")
            self._param_columns(pds)
        for s in self.sources:
            dss.append(self._datasource(s))
        if self.actions:
            wb.append(self._actions())
        wss = ET.SubElement(wb, "worksheets")
        for sh in self.sheets:
            wss.append(self._worksheet(sh))
        dbs = ET.SubElement(wb, "dashboards")
        for d in self.dashboards:
            dbs.append(self._dashboard(d))
        wb.append(self._windows())
        tree = ET.ElementTree(wb)
        ET.indent(tree, "  ")
        with open(path, "wb") as fh:
            fh.write(b"<?xml version='1.0' encoding='utf-8' ?>\n\n")
            tree.write(fh, encoding="utf-8", xml_declaration=False)


def build_workbook(directory: Path, path: Path) -> None:
    from .dashboards import design

    design(directory).write(path)
