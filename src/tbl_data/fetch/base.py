"""Fetcher interface shared by the browser, HTTP and file backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Resource:
    """One remote JSON document, addressed by (source, kind, rid)."""

    source: str  # "tbf" | "genius"
    kind: str  # e.g. "match_header", "pbp"
    rid: str  # resource id within kind, e.g. "345960" or "174_316467"
    url: str  # absolute URL
    method: str = "GET"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.source, self.kind, self.rid)


@dataclass
class FetchResult:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: int = 0
    challenge: bool = False  # Cloudflare served a challenge instead of data
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.challenge and self.error is None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


class Fetcher(Protocol):
    source: str

    def fetch(self, res: Resource, etag: str | None = None) -> FetchResult: ...

    def close(self) -> None: ...


class ChallengeBlocked(RuntimeError):
    """Raised when Cloudflare keeps challenging; the sync stops and can resume later."""
