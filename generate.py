#!/usr/bin/env python3
"""Top 10 Today — a static Stremio/Nuvio catalog addon with Netflix-style ranking artwork.

Builds `dist/` (manifest + two catalogs + numbered images). Hosted on GitHub Pages and rebuilt on a schedule,
so the lists and artwork stay current without a server.

Data source (global, right now):
  * TMDB "Trending today"  — used when the TMDB_API_KEY environment variable is set
  * Cinemeta "Popular"     — Stremio's own popularity feed, no key needed (default)

Environment:
  SITE_URL       public base URL of the deployed site, e.g. https://you.github.io/nuvio-top10-addon  (required)
  TMDB_API_KEY   optional TMDB v3 API key
  TOP_N          how many ranks to publish (default 10)
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
FONT = ROOT / "fonts" / "ArchivoBlack-Regular.ttf"
SITE_URL = os.environ.get("SITE_URL", "").strip().rstrip("/")
TMDB_KEY = os.environ.get("TMDB_API_KEY", "").strip()
TOP_N = int(os.environ.get("TOP_N", "10"))
UA = "nuvio-top10-addon/1.0 (+https://github.com)"

POSTER_W, POSTER_H = 600, 900          # 2:3 card
WIDE_W, WIDE_H = 1280, 720             # 16:9 card
NUM_FILL = (24, 24, 24)
NUM_STROKE = (178, 178, 178)

TMDB_GENRES = {  # TMDB genre ids -> names (movie + tv)
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime", 99: "Documentary", 18: "Drama",
    10751: "Family", 14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance",
    878: "Science Fiction", 10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
    10759: "Action & Adventure", 10762: "Kids", 10763: "News", 10764: "Reality", 10765: "Sci-Fi & Fantasy",
    10766: "Soap", 10767: "Talk", 10768: "War & Politics",
}


# ----------------------------------------------------------------------------------------------- http
def http_get(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_json(url: str):
    return json.loads(http_get(url).decode("utf-8"))


# ----------------------------------------------------------------------------------------------- sources
def from_tmdb(kind: str) -> list[dict]:
    """kind: 'movie' | 'tv'. Returns normalized items ordered by trending rank."""
    data = http_json(f"https://api.themoviedb.org/3/trending/{kind}/day?api_key={TMDB_KEY}&language=en-US")
    items = []
    for r in data.get("results", []):
        if r.get("adult") or not r.get("poster_path"):
            continue
        ext = http_json(f"https://api.themoviedb.org/3/{kind}/{r['id']}/external_ids?api_key={TMDB_KEY}")
        imdb = (ext.get("imdb_id") or "").strip()
        date = r.get("release_date") if kind == "movie" else r.get("first_air_date")
        items.append({
            "id": imdb if imdb.startswith("tt") else f"tmdb:{r['id']}",
            "name": r.get("title") or r.get("name") or "",
            "poster": f"https://image.tmdb.org/t/p/w780{r['poster_path']}",
            "background": f"https://image.tmdb.org/t/p/w1280{r['backdrop_path']}" if r.get("backdrop_path") else None,
            "description": r.get("overview") or "",
            "year": (date or "")[:4],
            "rating": f"{r['vote_average']:.1f}" if r.get("vote_average") else None,
            "genres": [TMDB_GENRES[g] for g in r.get("genre_ids", []) if g in TMDB_GENRES],
        })
        if len(items) >= TOP_N:
            break
    return items


def from_cinemeta(kind: str) -> list[dict]:
    """kind: 'movie' | 'series'. Cinemeta's 'Popular' catalog, already ranked."""
    data = http_json(f"https://v3-cinemeta.strem.io/catalog/{kind}/top.json")
    items = []
    for m in data.get("metas", []):
        if not m.get("poster") or not str(m.get("imdb_id") or m.get("id", "")).startswith("tt"):
            continue
        year = str(m.get("releaseInfo") or m.get("year") or "")[:4]
        items.append({
            "id": m.get("imdb_id") or m["id"],
            "name": m.get("name", ""),
            "poster": m["poster"],
            "background": m.get("background"),
            "description": m.get("description") or "",
            "year": year,
            "rating": str(m["imdbRating"]) if m.get("imdbRating") else None,
            "genres": m.get("genres") or m.get("genre") or [],
        })
        if len(items) >= TOP_N:
            break
    return items


# ----------------------------------------------------------------------------------------------- artwork
def fetch_image(url: str | None) -> Image.Image | None:
    if not url:
        return None
    try:
        return Image.open(io.BytesIO(http_get(url))).convert("RGB")
    except Exception as e:  # noqa: BLE001
        print(f"  ! image failed {url}: {e}", file=sys.stderr)
        return None


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Resize + center-crop to exactly w x h."""
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def font_for(text: str, max_w: int, max_h: int) -> ImageFont.FreeTypeFont:
    size = max_h
    while size > 10:
        f = ImageFont.truetype(str(FONT), size)
        l, t, r, b = f.getbbox(text)
        if (r - l) <= max_w and (b - t) <= max_h:
            return f
        size -= 4
    return ImageFont.truetype(str(FONT), 10)


def draw_number(canvas: Image.Image, text: str, box: tuple[int, int, int, int], align: str) -> None:
    """Netflix-style numeral: dark fill, grey outline, soft shadow. box=(x0,y0,x1,y1); align='left'|'right'."""
    x0, y0, x1, y1 = box
    f = font_for(text, x1 - x0, y1 - y0)
    l, t, r, b = f.getbbox(text)
    tw, th = r - l, b - t
    x = (x0 - l) if align == "left" else (x1 - r)
    y = y1 - b                                   # sit on the bottom of the box
    stroke = max(4, round(th * 0.045))
    # shadow
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((x + stroke, y + stroke * 1.5), text, font=f, fill=(0, 0, 0, 170),
                                stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
    shadow = shadow.filter(ImageFilter.GaussianBlur(stroke * 2))
    canvas.alpha_composite(shadow)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((x, y), text, font=f, fill=NUM_FILL + (255,), stroke_width=stroke,
                               stroke_fill=NUM_STROKE + (255,))
    canvas.alpha_composite(layer)


def gradient(w: int, h: int, direction: str, strength: int = 230) -> Image.Image:
    """Transparent -> black gradient. direction: 'left' (dark on left) or 'bottom'."""
    g = Image.new("L", (w, h), 0)
    px = g.load()
    for i in range(w if direction == "left" else h):
        a = int(strength * (1 - i / (w if direction == "left" else h)) ** 1.6)
        if direction == "left":
            for j in range(h):
                px[i, j] = a
        else:
            for j in range(w):
                px[j, h - 1 - i] = a
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    black.putalpha(g)
    return black


def render_poster_card(poster: Image.Image, rank: int) -> Image.Image:
    """2:3 card: the poster with the rank numeral in the lower-left (Netflix mobile style)."""
    card = cover(poster, POSTER_W, POSTER_H).convert("RGBA")
    card.alpha_composite(gradient(POSTER_W, POSTER_H, "bottom", 200))
    text = str(rank)
    box_h = int(POSTER_H * (0.32 if rank < 10 else 0.27))
    draw_number(card, text, (26, POSTER_H - 22 - box_h, int(POSTER_W * 0.80), POSTER_H - 22), "left")
    return card.convert("RGB")


def dominant_tint(img: Image.Image) -> tuple[int, int, int]:
    small = img.resize((24, 24)).convert("RGB")
    px = list(small.getdata())
    r = sum(p[0] for p in px) // len(px); g_ = sum(p[1] for p in px) // len(px); b = sum(p[2] for p in px) // len(px)
    return (min(255, r + 10), min(255, g_ + 10), min(255, b + 10))


def render_wide_card(poster: Image.Image, backdrop: Image.Image | None, rank: int) -> Image.Image:
    """16:9 card: big numeral on the left, poster on the right (the Netflix TV Top 10 tile)."""
    base = Image.new("RGBA", (WIDE_W, WIDE_H), (16, 16, 16, 255))
    # soft colour glow taken from the poster, so each tile has its own tint like the Netflix row
    tint = dominant_tint(poster)
    glow = Image.new("RGBA", (WIDE_W, WIDE_H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-WIDE_W * 0.1, -WIDE_H * 0.2, WIDE_W * 1.05, WIDE_H * 1.3), fill=tint + (70,))
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(160)))
    base.alpha_composite(gradient(WIDE_W, WIDE_H, "bottom", 110))
    poster_w = int(WIDE_H * 2 / 3)                       # 480
    px = WIDE_W - poster_w - 36
    p = cover(poster, poster_w, WIDE_H - 36).convert("RGBA")
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle((px - 12, 6, px + poster_w + 12, WIDE_H), fill=(0, 0, 0, 180))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(16)))
    base.alpha_composite(p, (px, 18))
    text = str(rank)
    draw_number(base, text, (48, int(WIDE_H * 0.14), px - 24, WIDE_H - 18), "right")
    return base.convert("RGB")


def render_logo() -> Image.Image:
    img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    draw_number(img, "10", (20, 150, 492, 440), "right")
    f = ImageFont.truetype(str(FONT), 86)
    d = ImageDraw.Draw(img)
    d.text((26, 92), "TOP", font=f, fill=(235, 235, 235, 255))
    return img


# ----------------------------------------------------------------------------------------------- build
def build_catalog(kind: str, catalog_id: str, items: list[dict]) -> dict:
    metas = []
    for rank, it in enumerate(items, start=1):
        print(f"  #{rank:<2} {it['name']} ({it['year']}) {it['id']}")
        poster_img = fetch_image(it["poster"])
        if poster_img is None:
            continue
        backdrop_img = fetch_image(it["background"])
        slug = it["id"].replace(":", "-")
        card_name = f"{kind}-{rank}-{slug}.jpg"
        wide_name = f"{kind}-{rank}-{slug}-wide.jpg"
        render_poster_card(poster_img, rank).save(DIST / "img" / card_name, "JPEG", quality=88, optimize=True)
        render_wide_card(poster_img, backdrop_img, rank).save(DIST / "img" / wide_name, "JPEG", quality=88, optimize=True)
        rank_word = "#1" if rank == 1 else f"#{rank}"
        metas.append({
            "id": it["id"],
            "type": kind,
            "name": it["name"],
            "poster": f"{SITE_URL}/img/{card_name}",
            "posterShape": "poster",
            "landscapePoster": f"{SITE_URL}/img/{wide_name}",
            "background": f"{SITE_URL}/img/{wide_name}",
            "description": f"{rank_word} in the world today. {it['description']}".strip(),
            "releaseInfo": it["year"] or None,
            "imdbRating": it["rating"],
            "genres": it["genres"],
        })
    return {"metas": metas}


def main() -> int:
    if not SITE_URL:
        print("SITE_URL is required (e.g. https://you.github.io/nuvio-top10-addon)", file=sys.stderr)
        return 2
    for d in ("catalog/movie", "catalog/series", "img"):
        (DIST / d).mkdir(parents=True, exist_ok=True)
    for old in (DIST / "img").glob("*.jpg"):
        old.unlink()

    source = "TMDB Trending (today)" if TMDB_KEY else "Cinemeta Popular"
    print(f"source: {source}")
    movies = from_tmdb("movie") if TMDB_KEY else from_cinemeta("movie")
    series = from_tmdb("tv") if TMDB_KEY else from_cinemeta("series")

    print("movies:")
    (DIST / "catalog/movie/top10-movies.json").write_text(
        json.dumps(build_catalog("movie", "top10-movies", movies), ensure_ascii=False, indent=1), encoding="utf-8")
    print("series:")
    (DIST / "catalog/series/top10-series.json").write_text(
        json.dumps(build_catalog("series", "top10-series", series), ensure_ascii=False, indent=1), encoding="utf-8")

    render_logo().save(DIST / "logo.png", "PNG")
    now = dt.datetime.now(dt.timezone.utc)
    manifest = {
        "id": "community.top10today",
        "version": f"1.{now.strftime('%Y%m%d')}.{now.strftime('%H%M')}",
        "name": "Top 10 Today",
        "description": f"The {TOP_N} most-watched movies and series in the world right now, with ranked artwork. "
                       f"Source: {source}. Rebuilt automatically.",
        "logo": f"{SITE_URL}/logo.png",
        "resources": ["catalog"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt", "tmdb:"],
        "catalogs": [
            {"type": "movie", "id": "top10-movies", "name": "Top 10 Movies Today"},
            {"type": "series", "id": "top10-series", "name": "Top 10 Series Today"},
        ],
        "behaviorHints": {"configurable": False, "configurationRequired": False},
    }
    (DIST / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (DIST / ".nojekyll").write_text("")
    (DIST / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Top 10 Today — Nuvio addon</title>"
        "<body style='font-family:system-ui;background:#111;color:#eee;padding:2rem'>"
        "<h1>Top 10 Today</h1><p>Install in Nuvio / Stremio with this manifest URL:</p>"
        f"<p><code style='font-size:1.1rem'>{SITE_URL}/manifest.json</code></p>"
        f"<p>Source: {source} · built {now.strftime('%Y-%m-%d %H:%M UTC')}</p>"
        "<p><a href='catalog/movie/top10-movies.json' style='color:#9cf'>movies.json</a> · "
        "<a href='catalog/series/top10-series.json' style='color:#9cf'>series.json</a></p></body>",
        encoding="utf-8")
    print(f"done -> {DIST}  ({manifest['version']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
