"""Content-addressed raw store: raw/{source}/{kind}/{rid}/{sha256[:16]}.json.gz.

Responses are written before any parsing and are never overwritten; a changed
response becomes a new file next to the old one.
"""

from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


class RawStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path(self, source: str, kind: str, rid: str, digest: str) -> Path:
        return self.root / source / kind / rid / f"{digest[:16]}.json.gz"

    def put(self, source: str, kind: str, rid: str, body: bytes) -> str:
        digest = sha256(body)
        p = self.path(source, kind, rid, digest)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            # mtime=0 keeps the gzip bytes deterministic for identical bodies
            tmp.write_bytes(gzip.compress(body, mtime=0))
            os.replace(tmp, p)
        return digest

    def get(self, source: str, kind: str, rid: str, digest: str) -> bytes:
        return gzip.decompress(self.path(source, kind, rid, digest).read_bytes())
