"""SQLite manifest: what was fetched, when, what it hashed to, and what is due next."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    source TEXT NOT NULL, kind TEXT NOT NULL, rid TEXT NOT NULL,
    url TEXT NOT NULL, method TEXT NOT NULL DEFAULT 'GET',
    scope TEXT,                      -- e.g. 'tbl/2026-27'
    status TEXT,                     -- ok | empty | missing | error
    http_status INTEGER,
    sha256 TEXT,                     -- latest stored body
    etag TEXT,
    first_fetched TEXT, last_fetched TEXT, last_changed TEXT,
    n_fetches INTEGER NOT NULL DEFAULT 0,
    n_versions INTEGER NOT NULL DEFAULT 0,
    final INTEGER NOT NULL DEFAULT 0, -- 1 = never refetch unless forced
    next_due TEXT,
    PRIMARY KEY (source, kind, rid)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY,
    run_id TEXT, source TEXT, kind TEXT, rid TEXT, url TEXT,
    fetched_at TEXT, http_status INTEGER, challenge INTEGER, sha256 TEXT,
    changed INTEGER, bytes INTEGER, elapsed_ms INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, args TEXT,
    requests INTEGER, new_versions INTEGER, challenges INTEGER, errors INTEGER, outcome TEXT
);
CREATE TABLE IF NOT EXISTS matches (
    match_id INTEGER PRIMARY KEY,
    scope TEXT, week TEXT, half_value TEXT, match_start TEXT,
    home_team_id INTEGER, home TEXT, away_team_id INTEGER, away TEXT,
    home_score INTEGER, away_score INTEGER,
    genius_id TEXT, youtube TEXT, broadcast TEXT, status_id INTEGER,
    played INTEGER NOT NULL DEFAULT 0, updated_at TEXT
);
"""


@dataclass
class ResourceState:
    status: str | None
    sha256: str | None
    etag: str | None
    final: bool
    next_due: str | None
    last_fetched: str | None


class Manifest:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # -- resources -----------------------------------------------------------------
    def state(self, source: str, kind: str, rid: str) -> ResourceState | None:
        r = self.db.execute(
            "SELECT status, sha256, etag, final, next_due, last_fetched FROM resources"
            " WHERE source=? AND kind=? AND rid=?",
            (source, kind, rid),
        ).fetchone()
        if r is None:
            return None
        return ResourceState(r["status"], r["sha256"], r["etag"], bool(r["final"]), r["next_due"], r["last_fetched"])

    def record(
        self,
        *,
        run_id: str,
        source: str,
        kind: str,
        rid: str,
        url: str,
        method: str,
        scope: str,
        now: datetime,
        http_status: int,
        status: str,
        sha: str | None,
        etag: str | None,
        changed: bool,
        final: bool,
        next_due: datetime | None,
        nbytes: int,
        elapsed_ms: int,
        challenge: bool = False,
        error: str | None = None,
    ) -> None:
        ts = now.isoformat()
        due = next_due.isoformat() if next_due else None
        with self.db:
            self.db.execute(
                "INSERT INTO fetch_log (run_id, source, kind, rid, url, fetched_at, http_status, challenge,"
                " sha256, changed, bytes, elapsed_ms, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, source, kind, rid, url, ts, http_status, int(challenge), sha, int(changed), nbytes, elapsed_ms, error),
            )
            self.db.execute(
                """INSERT INTO resources (source, kind, rid, url, method, scope, status, http_status, sha256, etag,
                       first_fetched, last_fetched, last_changed, n_fetches, n_versions, final, next_due)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)
                   ON CONFLICT (source, kind, rid) DO UPDATE SET
                       url=excluded.url, method=excluded.method, scope=excluded.scope,
                       status=excluded.status, http_status=excluded.http_status,
                       sha256=COALESCE(excluded.sha256, resources.sha256),
                       etag=COALESCE(excluded.etag, resources.etag),
                       last_fetched=excluded.last_fetched,
                       last_changed=CASE WHEN ? THEN excluded.last_fetched ELSE resources.last_changed END,
                       n_fetches=resources.n_fetches + 1,
                       n_versions=resources.n_versions + ?,
                       final=excluded.final, next_due=excluded.next_due""",
                (source, kind, rid, url, method, scope, status, http_status, sha, etag,
                 ts, ts, ts if changed else None, int(changed), int(final), due,
                 int(changed), int(changed)),
            )

    def unfinalize(self, source: str, kind: str) -> int:
        with self.db:
            return self.db.execute(
                "UPDATE resources SET final=0, next_due=NULL WHERE source=? AND kind=?", (source, kind)
            ).rowcount

    def latest_sha(self, source: str, kind: str, rid: str) -> str | None:
        st = self.state(source, kind, rid)
        return st.sha256 if st else None

    # -- matches -------------------------------------------------------------------
    def upsert_match(self, row: dict) -> None:
        cols = list(row)
        with self.db:
            self.db.execute(
                f"INSERT INTO matches ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
                f" ON CONFLICT (match_id) DO UPDATE SET {','.join(f'{c}=excluded.{c}' for c in cols if c != 'match_id')}",
                [row[c] for c in cols],
            )

    def matches(self, scope: str, played_only: bool = False) -> list[sqlite3.Row]:
        q = "SELECT * FROM matches WHERE scope=?" + (" AND played=1" if played_only else "") + " ORDER BY match_start, match_id"
        return self.db.execute(q, (scope,)).fetchall()

    # -- runs ----------------------------------------------------------------------
    def start_run(self, run_id: str, started: datetime, args: dict) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO runs (run_id, started_at, args) VALUES (?,?,?)",
                (run_id, started.isoformat(), json.dumps(args, ensure_ascii=False)),
            )

    def finish_run(self, run_id: str, finished: datetime, **stats) -> None:
        with self.db:
            self.db.execute(
                "UPDATE runs SET finished_at=?, requests=?, new_versions=?, challenges=?, errors=?, outcome=?"
                " WHERE run_id=?",
                (finished.isoformat(), stats["requests"], stats["new_versions"], stats["challenges"],
                 stats["errors"], stats["outcome"], run_id),
            )
