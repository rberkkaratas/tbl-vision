# AGENTS.md

Instructions for coding agents working in this repository. The full manual is
[docs/for-ai/README.md](docs/for-ai/README.md); read it before changing code, and
[docs/for-ai/tableau-xml.md](docs/for-ai/tableau-xml.md) before touching the workbook
writer.

The rules that matter most:

- No match data in git: raw responses, Parquet, CSV/Hyper, logos and `tests/fixtures/`
  stay in `$TBL_DATA_DIR` (default `~/data/tbl`).
- Do not run `tbl sync` (network, Chrome, Cloudflare) without the human; `build` and
  `analyze` are offline and safe to rerun.
- Fix feed errors with `overrides/<match_id>.json`, never by editing raw files.
- Blocking validation checks must pass; add a check with every new rule or table.
- Analyse seasons in order (2025-26 before 2026-27).
- Dashboard text is Turkish and never gives verdicts: describe numbers with league
  context and data strength.
- Verify with `uv run pytest`; quote numbers from `metrics.json` or the validation
  output, not from memory.
