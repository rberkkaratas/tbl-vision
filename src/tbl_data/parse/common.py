from __future__ import annotations


def to_int(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(str(v).strip())
    except ValueError:
        return None


def minutes_to_s(v: str | None) -> int:
    """'27:55' -> 1675. Sources sometimes emit '46:60'; that is 47:00."""
    if not v:
        return 0
    parts = str(v).split(":")
    m, s = int(parts[-2] if len(parts) > 1 else 0), int(parts[-1])
    h = int(parts[0]) if len(parts) == 3 else 0
    return h * 3600 + m * 60 + s


def clean(v) -> str | None:
    """Strip and collapse whitespace; '' -> None."""
    if v is None:
        return None
    s = " ".join(str(v).split())
    return s or None
