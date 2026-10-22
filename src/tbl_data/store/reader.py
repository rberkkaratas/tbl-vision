"""Read the latest stored body of each resource (no network)."""

from __future__ import annotations

import json
from collections.abc import Iterator

from .manifest import Manifest
from .raw import RawStore


class RawReader:
    def __init__(self, store: RawStore, manifest: Manifest, scope: str):
        self.store, self.m, self.scope = store, manifest, scope

    def get(self, source: str, kind: str, rid: str) -> dict | None:
        sha = self.m.latest_sha(source, kind, rid)
        return json.loads(self.store.get(source, kind, rid, sha)) if sha else None

    def iter(self, source: str, kind: str) -> Iterator[tuple[str, dict]]:
        rows = self.m.db.execute(
            "SELECT rid, sha256 FROM resources WHERE scope=? AND source=? AND kind=? AND sha256 IS NOT NULL"
            " AND status='ok' ORDER BY rid",
            (self.scope, source, kind),
        ).fetchall()
        for r in rows:
            yield r["rid"], json.loads(self.store.get(source, kind, r["rid"], r["sha256"]))
