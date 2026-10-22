"""Genius LiveStats fetcher: plain HTTPS with retries and ETag revalidation."""

from __future__ import annotations

import time

import httpx
import stamina

from .base import FetchResult, Resource


class _Retryable(Exception):
    pass


class HttpxFetcher:
    def __init__(self, source: str = "genius", min_interval: float = 0.5, timeout: float = 30.0):
        self.source = source
        self.min_interval = min_interval
        self._last = 0.0
        self._client = httpx.Client(timeout=timeout, follow_redirects=True, headers={"Accept": "application/json"})

    def fetch(self, res: Resource, etag: str | None = None) -> FetchResult:
        headers = {"If-None-Match": etag} if etag else {}
        try:
            r = self._get(res.url, headers)
        except (httpx.HTTPError, _Retryable) as e:
            return FetchResult(status=0, error=f"{type(e).__name__}: {e}")
        return FetchResult(
            status=r.status_code,
            body=r.content if r.status_code == 200 else b"",
            headers={k.lower(): v for k, v in r.headers.items()},
            elapsed_ms=int(r.elapsed.total_seconds() * 1000),
        )

    @stamina.retry(on=(httpx.TransportError, _Retryable), attempts=4, wait_initial=1.0, wait_max=20.0)
    def _get(self, url: str, headers: dict[str, str]) -> httpx.Response:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        r = self._client.get(url, headers=headers)
        if r.status_code in (429, 500, 502, 503, 504):
            raise _Retryable(f"HTTP {r.status_code}")
        return r

    def close(self) -> None:
        self._client.close()
