"""tbl command line: sync raw data, show status."""

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
                    with_players=players, only=only).run()
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
