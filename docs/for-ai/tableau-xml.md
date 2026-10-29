# Writing Tableau workbook XML

`analysis/workbook.py` writes `.twb` files by hand. Tableau has no public API for
building views, and it validates the document on open against an XSD shipped
inside the app (`Tableau Public.app/Contents/Resources/tablangres.rcc`). Every rule
below was verified against Tableau Public 2026.3; most cost a failed open to learn.
Follow them, and when Tableau rejects a file, read its error, find the element here,
and check the XSD before guessing.

## Document

- `version` / `original-version` = `18.1`; workbook `locale="tr_TR"` (decimal commas on
  axes).
- Use only elements and attributes seen in workbooks Tableau itself saved (2024.3-2026.3).
  When you need something new, build it once in Tableau, save, and copy the pattern.
  Tableau's autosave (`.twbr` next to the workbook) is a quick way to see what it wrote.
- **Tableau Public saves only extract-backed data sources.** Every CSV source gets a Hyper
  extract (`tableauhyperapi`), written next to the CSV. Datetime columns in some outputs
  broke the extract; viewer tables carry formatted text or epoch integers instead
  (`fixture_ts`).

## Actions

- Inside `<actions>` the order is fixed by the schema: filter `<action>` elements, then
  `<nav-action>`, then `<edit-group-action>`, then `<edit-parameter-action>`. Out of
  order fails with "element 'action' is not allowed for content model ...". The writer
  sorts them; keep it that way.
- **Go to Sheet** is a `<nav-action>`:
  `<activation type='on-select'/>`, `<source dashboard=... worksheet=... type='sheet'/>`,
  `<params><param name='sheet' value='<target dashboard>'/></params>`. The param name is
  `sheet`; `target` does not navigate.
- **Never navigate with a filter action aimed at another dashboard.** It works, but the
  filter it leaves on the target sheet empties tables there afterwards.
- Parameter actions (`edit-parameter-action`) set `Ana takım`, `Rakip`, `Oyuncu`, `Maç`
  from a clicked mark; the source field must be on the sheet (Detail is enough).

## Dashboards and zones

- **A worksheet may appear only once per dashboard.** Twice gives "Workbook dashboard
  contains duplicate visual element IDs ... Error Code: 20A26011". Make a second sheet
  (for example `Logo · ana takım (maç)` next to `Logo · ana takım`).
- Image zone: `type-v2='bitmap'`, `param=<absolute path>`, `is-scaled='1'`,
  `is-centered='1'`.
- Fixed-size dashboards of equal height. A taller page gets a vertical scrollbar, which
  narrows it and makes everything jump sideways when switching pages.

## Marks and styles

- **Custom shapes** (team logos): in the data source `<style>`, an
  `<encoding attr='shape' type='shape' field=...>` with `<map to='TBL Logolar/<file>.png'>`
  per value. `type='palette'` (the colour form) silently draws nothing.
- Shapes must also be embedded for Tableau Public: `<external><shapes><shape
  name='TBL Logolar/<file>.png'>base64</shape>` placed after `<windows>`.
- Shape size: a large `mark_size` makes shapes disappear; `LOGO_SIZE = 2.6` fills a logo
  zone without clipping.
- Gridlines and zero lines off: worksheet style rules `gridline` / `zeroline` with
  `line-visibility=off`. Area marks are translucent by default; set pane
  `mark-transparency` to `255` for a solid header band.
- A `[none:x:nk]` key used in a colour or shape map needs its column instance declared in
  the data source.

## Formulas

- Parameters are referenced as `[Parameters].[Parameter N]`; in `dashboards.py` formulas
  use `{Caption}` and the writer resolves them. Tokens `[[RAKIP]]` and `[[MAC]]` expand
  to CASE expressions over the fixture list.
- Tableau's `STR()` always uses a decimal point. Format viewer numbers in Python
  (`presentation.py`) and place the text.
- Header cells cannot show tooltips; put definitions on marks (`term_tr`, `term_desc`).

## When Tableau refuses a file

1. Read the message: it names the element and the expected content model.
2. Search the XSD inside `tablangres.rcc` for that element to see allowed children and
   their order.
3. If the feature is new to this repo, build it in Tableau by hand, save, diff the XML.
4. Add a structural test in `tests/test_analysis.py` (the existing ones check sheet and
   dashboard counts, action order and no duplicate sheet per dashboard).
