"""tbl command line: sync raw data, build tables, analyse, show status."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import structlog
import typer

from .config import data_dir, get_season
from .fetch.browser import BrowserFetcher
from .fetch.files import FileFetcher
from .fetch.http import HttpxFetcher
from .ingest import Syncer, summarize
from .store import Manifest, RawStore

app = typer.Typer(no_args_is_help=True, add_completion=False)

DataDir = Annotated[Path | None, typer.Option("--data-dir", help="Default: $TBL_DATA_DIR or ~/data/tbl")]


def _setup_logging(verbose: bool) -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG if verbose else logging.INFO),
    )


@app.command()
def sync(
    season: Annotated[str, typer.Option(help="Season key, e.g. 2026-27")] = "2026-27",
    league: Annotated[str, typer.Option(help="TBF league prefix")] = "tbl",
    data: DataDir = None,
    only: Annotated[str | None, typer.Option(help="Only fetch from 'tbf' or 'genius'")] = None,
    max_requests: Annotated[int | None, typer.Option(help="Stop after N requests")] = None,
    players: Annotated[bool, typer.Option(help="Fetch player profiles")] = True,
    player_games: Annotated[bool, typer.Option(help="Also fetch each player's season game log (repeats box scores)")] = False,
    refresh_season: Annotated[bool, typer.Option(help="Refetch fixtures/teams now, ignoring TTL")] = False,
    refresh_players: Annotated[bool, typer.Option(help="Refetch all player profiles")] = False,
    headless: Annotated[bool, typer.Option(help="Run Chrome headless (Cloudflare blocks it more often)")] = False,
    offline: Annotated[Path | None, typer.Option(help="Serve requests from a fixture dir instead of the network")] = None,
    verbose: bool = False,
) -> None:
    """Fetch everything due for one season into the raw store."""
    _setup_logging(verbose)
    s = get_season(league, season)
    root = data or data_dir()
    if offline:
        tbf, genius = FileFetcher(offline, "tbf"), FileFetcher(offline, "genius")
    else:
        tbf = BrowserFetcher(s.landing_url, root / "browser-profile", headless=headless)
        genius = HttpxFetcher()
    manifest = Manifest(root / "manifest.sqlite")
    try:
        st = Syncer(s, RawStore(root / "raw"), manifest, tbf, genius, max_requests=max_requests,
                    refresh_season=refresh_season, refresh_players=refresh_players,
                    with_players=players, player_games=player_games, only=only).run()
    finally:
        tbf.close()
        genius.close()
    typer.echo(
        f"\nrun {st.run_id}: {st.outcome} | requests {st.requests} | new versions {st.new_versions} | "
        f"304 {st.not_modified} | challenges {st.challenges} | errors {st.errors}"
    )
    for kind, n in sorted(st.by_kind.items()):
        typer.echo(f"  {kind:<24} {n}")
    _print_status(manifest, s)
    manifest.close()
    if st.outcome == "blocked":
        raise typer.Exit(2)


@app.command()
def build(
    season: str = "2026-27",
    league: str = "tbl",
    data: DataDir = None,
    verbose: bool = False,
) -> None:
    """raw -> staged -> enriched Parquet + DuckDB views, then validation."""
    import polars as pl

    from . import build as B
    from . import validate as V
    from .config import OVERRIDES_DIR
    from .enrich import enrich
    from .store.reader import RawReader

    _setup_logging(verbose)
    s = get_season(league, season)
    root = data or data_dir()
    m = Manifest(root / "manifest.sqlite")
    scope = f"{s.league}/{s.key}"
    played = [(r["match_id"], int(r["genius_id"]) if r["genius_id"] else None) for r in m.matches(scope, played_only=True)]
    staged = B.build(RawReader(RawStore(root / "raw"), m, scope), s, played)
    m.close()
    enriched = enrich(staged.tables, OVERRIDES_DIR).tables
    out = B.write(staged, root, s, enriched)
    checks = V.run_all(staged.tables, pl.DataFrame(staged.drift, schema=["object", "key", "n", "first_seen"]),
                       staged.unmapped) + V.run_enriched(staged.tables, enriched)
    V.report(checks).write_parquet(out / "validation.parquet")

    typer.echo(f"\nstaged + enriched -> {out.parent.parent.parent}")
    for name, df in {**staged.tables, **enriched}.items():
        typer.echo(f"  {name:<16} {df.height:>7} rows")
    typer.echo("\nvalidation")
    for c in checks:
        mark = "ok  " if c.ok else ("FAIL" if c.blocking else "note")
        typer.echo(f"  {mark} {c.name:<22} {c.failures.height:>4} / {c.checked:<6} {c.description}")
    with pl.Config(tbl_rows=5, tbl_width_chars=160):
        for c in checks:
            if not c.ok:
                typer.echo(f"\n{c.name}:\n{c.failures.head(5)}")
    typer.echo(f"\nquery: uv run tbl sql \"SELECT * FROM matches LIMIT 5\"   (DuckDB: {root / 'tbl.duckdb'})")
    if any(c.blocking and not c.ok for c in checks):
        raise typer.Exit(1)


@app.command()
def analyze(
    season: str = "2026-27",
    league: str = "tbl",
    data: DataDir = None,
    tableau: Annotated[bool, typer.Option(help="Also write CSVs + court image for Tableau")] = True,
    verbose: bool = False,
) -> None:
    """enriched -> analysis tables (xPTS, pairs, game flow, clutch) + Tableau CSVs, then validation."""
    import json

    from . import build as B
    from . import validate as V
    from .analysis import analyze as run_analysis
    from .analysis.tableau import export

    _setup_logging(verbose)
    s = get_season(league, season)
    root = data or data_dir()
    staged, enriched = B.read_layer(root, "staged", s), B.read_layer(root, "enriched", s)
    a = run_analysis(staged, enriched)
    out = B.write_layer(root, "analysis", s, a.tables)
    (out / "metrics.json").write_text(json.dumps(a.metrics, indent=2))
    typer.echo(f"\nanalysis -> {out}")
    for name, df in a.tables.items():
        typer.echo(f"  {name:<22} {df.height:>7} rows")
    typer.echo(f"\nxPTS model: {json.dumps(a.metrics['xpts'])}")
    if tableau:
        tdir = root / "tableau" / f"{s.league}-{s.key}"
        n = export(staged, enriched, a.tables, tdir)
        typer.echo(f"\ntableau -> {tdir}  ({', '.join(f'{k} {v}' for k, v in n.items())}, court.png)")
    checks = V.run_analysis(staged, enriched, a.tables, a.metrics)
    typer.echo("\nvalidation")
    for c in checks:
        mark = "ok  " if c.ok else ("FAIL" if c.blocking else "note")
        typer.echo(f"  {mark} {c.name:<22} {c.failures.height:>4} / {c.checked:<6} {c.description}")
    for c in checks:
        if not c.ok:
            typer.echo(f"\n{c.name}:\n{c.failures.head(5)}")
    if any(c.blocking and not c.ok for c in checks):
        raise typer.Exit(1)


@app.command()
def sql(query: str, data: DataDir = None) -> None:
    """Run SQL against the staged DuckDB views."""
    import duckdb

    con = duckdb.connect(str((data or data_dir()) / "tbl.duckdb"), read_only=True)
    try:
        con.sql(query).show(max_rows=50, max_width=200)
    except duckdb.Error as e:
        typer.echo(f"{type(e).__name__}: {e}", err=True)
        raise typer.Exit(1) from None
    finally:
        con.close()


@app.command()
def status(season: str = "2026-27", league: str = "tbl", data: DataDir = None) -> None:
    """Show what is in the raw store for a season."""
    s = get_season(league, season)
    m = Manifest((data or data_dir()) / "manifest.sqlite")
    _print_status(m, s)
    m.close()


def _print_status(m: Manifest, s) -> None:
    info = summarize(m, f"{s.league}/{s.key}")
    mt = info["matches"]
    typer.echo(f"\n{s.league} {s.key}: {mt['n']} matches in fixture, {mt['played'] or 0} played "
               f"({mt['genius'] or 0} with Genius id, {mt['youtube'] or 0} with YouTube link)")
    cols = ("ok", "empty", "missing", "error")
    rows: dict[tuple[str, str], dict[str, int]] = {}
    for r in info["resources"]:
        row = rows.setdefault((r["source"], r["kind"]), {"final": 0})
        row[r["status"]] = r["n"]
        row["final"] += r["final"]
    typer.echo(f"  {'source':<7} {'kind':<24}" + "".join(f"{c:>8}" for c in (*cols, "final")))
    for (src, kind), row in rows.items():
        typer.echo(f"  {src:<7} {kind:<24}" + "".join(f"{row.get(c, 0):>8}" for c in (*cols, "final")))
    for r in info["runs"][:3]:
        typer.echo(f"  run {r['run_id']} {r['started_at'][:16]} {r['outcome']}: {r['requests']} req, "
                   f"{r['new_versions']} new, {r['challenges']} challenges, {r['errors']} errors")


if __name__ == "__main__":
    app()
