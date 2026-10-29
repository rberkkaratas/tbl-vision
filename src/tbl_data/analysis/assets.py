"""Team logos, the TBL logo and team colours for the Tableau workbook.

Logos: FIBA LiveStats (Genius) serves each team's logo with the game data (tm.logoS, 200 x 200 PNG,
plain HTTPS); tm "1" is the TBF home team, so a team's logo is taken from one of its own games.
TBF's logo files sit behind Cloudflare. The TBL logo is the league's mark from Wikipedia (fair use).

Tableau draws a logo as a custom shape: the files go to a shape palette folder in
My Tableau Repository/Shapes (Tableau Desktop reads them from there; a workbook saved to Tableau
Public carries them along).

Colours: TBF's kit colour whose hue the logo really carries (at least 10% of its coloured pixels),
else the logo's most common saturated hue, else TBF's colour; darkened until it reads on white
(WCAG 3:1) - yellow becomes ochre.
"""

from __future__ import annotations

import colorsys
import gzip
import json
import shutil
import urllib.request
from pathlib import Path

import polars as pl

TBL_LOGO_URL = "https://upload.wikimedia.org/wikipedia/en/7/72/Turkish_Basketball_League_logo.png"
SHAPES_DIR = Path.home() / "Documents" / "My Tableau Repository" / "Shapes"
PALETTE = "TBL Logolar"  # the shape palette's folder name
TBL = "TBL"  # the league's own logo in the palette
USER_AGENT = "tbl-vision/0.1 (personal research)"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def logo_file(team_season_id: int) -> str:
    return f"{team_season_id}.png"


def genius_logos(raw_dir: Path, matches: pl.DataFrame) -> dict[int, str]:
    """team_season_id -> logo URL from the newest stored data.json of a game the team played."""
    out: dict[int, str] = {}
    for m in matches.filter(pl.col("genius_id").is_not_null()).sort("start_utc", descending=True).iter_rows(named=True):
        files = sorted((raw_dir / "genius" / "livestats" / str(m["genius_id"])).glob("*.json.gz"),
                       key=lambda p: p.stat().st_mtime)
        if not files:
            continue
        tm = json.loads(gzip.decompress(files[-1].read_bytes()))["tm"]
        for side, team in (("1", m["home_team_id"]), ("2", m["away_team_id"])):
            url = (tm.get(side, {}).get("logoS") or {}).get("url")
            if url and team not in out:
                out[team] = url
    return out


def square(png: bytes) -> bytes:
    """Pads a logo to a square with transparency, so a shape mark does not stretch it."""
    from io import BytesIO

    from PIL import Image

    im = Image.open(BytesIO(png)).convert("RGBA")
    side = max(im.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - im.width) // 2, (side - im.height) // 2), im)
    buf = BytesIO()
    canvas.save(buf, "PNG")
    return buf.getvalue()


def fetch_logos(root: Path, matches: pl.DataFrame, teams: pl.DataFrame, refresh: bool = False) -> Path:
    """Downloads the logos once into <root>/assets/logos (team_season_id.png, TBL.png); returns that folder."""
    out = root / "assets" / "logos"
    out.mkdir(parents=True, exist_ok=True)
    urls = genius_logos(root / "raw", matches)
    for team in teams["team_season_id"]:
        path = out / logo_file(team)
        if team in urls and (refresh or not path.exists()):
            path.write_bytes(square(_get(urls[team])))
    tbl, wide = out / f"{TBL}.png", root / "assets" / f"{TBL}_wide.png"  # shape (square) and header image
    if refresh or not (tbl.exists() and wide.exists()):
        png = _get(TBL_LOGO_URL)
        wide.write_bytes(png)
        tbl.write_bytes(square(png))
    return out


def install_shapes(logos: Path, shapes_dir: Path = SHAPES_DIR) -> Path:
    """Copies the logos into the Tableau shape palette folder (replacing what was there)."""
    dest = shapes_dir / PALETTE
    dest.mkdir(parents=True, exist_ok=True)
    for f in logos.glob("*.png"):
        shutil.copy2(f, dest / f.name)
    return dest


def _luminance(r: float, g: float, b: float) -> float:
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _readable(hexv: str, contrast: float = 3.0) -> str:
    """Darkens a colour (hue and saturation kept) until its contrast with white is at least `contrast`
    (WCAG: 3:1 for marks and large text) - yellow kits become ochre."""
    r, g, b = (int(hexv.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    while l > 0.05 and 1.05 / (_luminance(*colorsys.hls_to_rgb(h, l, s)) + 0.05) < contrast:
        l -= 0.01
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return "#%02x%02x%02x" % tuple(round(x * 255) for x in (r, g, b))


def _tint(hexv: str, amount: float = 0.4) -> str:
    """A light version of a colour (towards white), for secondary marks."""
    r, g, b = (int(hexv.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % tuple(round(x + (255 - x) * amount) for x in (r, g, b))


def theme_color(c1: str | None, c2: str | None, fallback: str) -> str:
    """The club colour with the most colour in it (grey and white kits lose to the other colour)."""
    best, score = None, -1.0
    for c in (c1, c2):
        if not c or len(c.lstrip("#")) != 6:
            continue
        r, g, b = (int(c.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        sc = s * (1 - abs(l - 0.45))  # saturated and not washed out
        if sc > score:
            best, score = c, sc
    return _readable(best) if best and score > 0.05 else fallback


HUE_BINS = 24


def _hue_bin(r: float, g: float, b: float) -> int | None:
    h, l, s_ = colorsys.rgb_to_hls(r, g, b)
    return int(h * HUE_BINS) % HUE_BINS if s_ > 0.35 and 0.12 < l < 0.85 else None


def logo_hues(path: Path) -> dict[int, list[tuple[int, int, int]]]:
    """Coloured pixels of a logo by hue bin."""
    from PIL import Image

    bins: dict[int, list[tuple[int, int, int]]] = {}
    if path.exists():
        for r, g, b, a in Image.open(path).convert("RGBA").resize((100, 100)).getdata():
            k = _hue_bin(r / 255, g / 255, b / 255) if a >= 200 else None
            if k is not None:
                bins.setdefault(k, []).append((r, g, b))
    return bins


def pick_color(kit: list[str | None], logo: Path | None) -> str | None:
    """A kit colour the logo carries, else the logo's own main colour, else None."""
    bins = logo_hues(logo) if logo else {}
    total = sum(len(v) for v in bins.values())
    carried = []  # kit colours the logo shows, with their contrast on white (yellow kits lose to navy)
    for c in kit:
        if not c or len(c.lstrip("#")) != 6:
            continue
        rgb = tuple(int(c.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
        k = _hue_bin(*rgb)
        if k is None or not total:
            continue
        near = sum(len(bins.get((k + d) % HUE_BINS, [])) for d in (-1, 0, 1)) / total
        if near >= 0.10:
            carried.append((1.05 / (_luminance(*rgb) + 0.05), c))
    if carried:
        return max(carried)[1]
    if total >= 150:  # 1.5% of the logo
        px = max(bins.values(), key=len)
        return "#%02x%02x%02x" % tuple(round(sum(c[i] for c in px) / len(px)) for i in range(3))
    return None


def team_theme(teams: pl.DataFrame, codes: dict[str, str], logos: Path | None = None,
               fallback: str = "#e8590c") -> pl.DataFrame:
    """team (name), short name, theme colour, its light tint, the logo's shape file."""
    rows = []
    for r in teams.iter_rows(named=True):
        c = pick_color([r["color_1"], r["color_2"]], logos / logo_file(r["team_season_id"]) if logos else None)
        col = _readable(c) if c else theme_color(r["color_1"], r["color_2"], fallback)
        rows.append({"team": r["name"], "team_short": codes.get(r["name"], r["name"]), "color": col,
                     "color_light": _tint(col), "color_dark": _readable(col, contrast=6.0),  # header under white text
                     "logo": logo_file(r["team_season_id"])})
    return pl.DataFrame(rows)
