"""Offline fetcher: serves resources from {root}/{source}/{kind}/{rid}.json."""

from __future__ import annotations

from pathlib import Path

from .base import FetchResult, Resource


class FileFetcher:
    def __init__(self, root: Path, source: str):
        self.root = Path(root)
        self.source = source
        self.calls: list[Resource] = []

    def path_for(self, res: Resource) -> Path:
        return self.root / res.source / res.kind / f"{res.rid}.json"

    def fetch(self, res: Resource, etag: str | None = None) -> FetchResult:
        self.calls.append(res)
        p = self.path_for(res)
        if not p.exists():
            return FetchResult(status=404)
        return FetchResult(status=200, body=p.read_bytes(), headers={"content-type": "application/json"})

    def close(self) -> None:
        pass
