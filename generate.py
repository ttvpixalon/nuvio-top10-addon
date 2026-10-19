#!/usr/bin/env python3
"""Top 10 Today — a static Stremio/Nuvio catalog addon with Netflix-style ranking artwork.

Builds `dist/` (manifest + two catalogs + numbered images + state). Hosted on GitHub Pages and rebuilt on a schedule,
so the lists and artwork stay current without a server.

Ranking = consensus of every source that is available (weighted Borda count), so no single site's quirks decide
the list. Each source contributes its top 20; an item scores (21 - rank) x weight per source, totals are sorted.
A title that only one source lists (while several answered) is halved, so one feed's glitch can't push it in.
  * Trakt    "most watched" (unique users who actually played it, weekly)   weight 1.0   needs TRAKT_CLIENT_ID
  * Trakt    "trending" (users watching it in the last 24 h)                 weight 0.6   needs TRAKT_CLIENT_ID
  * TMDB     "trending today" (worldwide TMDB activity, released titles only) weight 0.8  needs TMDB_API_KEY
  * Cinemeta "popular" (Stremio's popularity feed, IMDb-based)               weight 0.7   always on, no key

Movement: the previous builds' rankings live in dist/state.json on the published site. Every build reads it back,
so each title knows whether it is NEW on the list, how far it moved since yesterday and how many days it has been
in the Top 10 — shown as a badge on the artwork and in the description.

Environment:
  SITE_URL          public base URL of the deployed site, e.g. https://you.github.io/nuvio-top10-addon  (required)
  TRAKT_CLIENT_ID   optional Trakt API app client id (free: trakt.tv/oauth/applications/new)
  TMDB_API_KEY      optional TMDB v3 API key (free: themoviedb.org/settings/api)
  TRAKT_PERIOD      daily | weekly | monthly | yearly  (default weekly)
  TOP_N             how many ranks to publish (default 10)
  SOURCES           comma list to force a subset: trakt,trakt_trending,tmdb,cinemeta (default: all configured)
  WIDE_STYLE        backdrop (backdrop + title logo + numeral, default) | poster (numeral + poster)
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
FONT = ROOT / "fonts" / "ArchivoBlack-Regular.ttf"
SITE_URL = os.environ.get("SITE_URL", "").strip().rstrip("/")
TMDB_KEY = os.environ.get("TMDB_API_KEY", "").strip()
TRAKT_ID = os.environ.get("TRAKT_CLIENT_ID", "").strip()
TRAKT_PERIOD = os.environ.get("TRAKT_PERIOD", "weekly").strip().lower() or "weekly"
TOP_N = int(os.environ.get("TOP_N", "10"))
WANTED = {s.strip().lower() for s in os.environ.get("SOURCES", "").split(",") if s.strip()}
WIDE_STYLE = os.environ.get("WIDE_STYLE", "backdrop").strip().lower() or "backdrop"
CANDIDATES = 20                        # ranks taken from each source before the consensus vote
SOLO_PENALTY = 0.5                     # score multiplier for a title only one source lists (when >1 answered)
HISTORY_DAYS = 30
TODAY = dt.datetime.now(dt.timezone.utc).date()
UA = "nuvio-top10-addon/3.0 (+https://github.com)"

POSTER_W, POSTER_H = 600, 900          # 2:3 card
WIDE_W, WIDE_H = 1280, 720             # 16:9 card
BG_W, BG_H = 1920, 1080                # plain backdrop (hero / expanded focus)
NUM_FILL = (24, 24, 24)
NUM_STROKE = (178, 178, 178)
BADGE_NEW = (214, 32, 44)
BADGE_UP = (26, 150, 70)
BADGE_DOWN = (82, 82, 82)

TMDB_GENRES = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime", 99: "Documentary", 18: "Drama",
    10751: "Family", 14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance",
    878: "Science Fiction", 10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
    10759: "Action & Adventure", 10762: "Kids", 10763: "News", 10764: "Reality", 10765: "Sci-Fi & Fantasy",
    10766: "Soap", 10767: "Talk", 10768: "War & Politics",
}


# ----------------------------------------------------------------------------------------------- http
def http_get(url: str, headers: dict | None = None, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_json(url: str, headers: dict | None = None):
    return json.loads(http_get(url, headers).decode("utf-8"))


# ----------------------------------------------------------------------------------------------- sources
# Every source returns an ordered list of candidate dicts:
#   {"id": "tt...", "name", "year", "description", "rating", "genres", "poster", "background", ["watchers"]}
def src_cinemeta(kind: str) -> list[dict]:
    typ = "movie" if kind == "movie" else "series"
    data = http_json(f"https://v3-cinemeta.strem.io/catalog/{typ}/top.json")
    out = []
    for m in data.get("metas", []):
        imdb = str(m.get("imdb_id") or m.get("id") or "")
        year = str(m.get("releaseInfo") or m.get("year") or "")[:4]
        if not imdb.startswith("tt") or (year.isdigit() and int(year) > TODAY.year):   # skip unreleased
            continue
        out.append({
            "id": imdb, "name": m.get("name", ""), "year": year,
            "description": m.get("description") or "", "rating": str(m["imdbRating"]) if m.get("imdbRating") else None,
            "genres": m.get("genres") or m.get("genre") or [], "poster": m.get("poster"), "background": m.get("background"),
        })
        if len(out) >= CANDIDATES:
            break
    return out


def src_tmdb(kind: str) -> list[dict]:
    t = "movie" if kind == "movie" else "tv"
    data = http_json(f"https://api.themoviedb.org/3/trending/{t}/day?api_key={TMDB_KEY}&language=en-US")
    out = []
    for r in data.get("results", []):
        date = (r.get("release_date") if t == "movie" else r.get("first_air_date")) or ""
        # TMDB "trending" counts page views, so an unreleased title with a hot trailer can sit at #1 even though
        # nobody can watch it yet. A Top 10 of what the world is watching should only hold released titles.
        if r.get("adult") or not date or date > TODAY.isoformat():
            continue
        ext = http_json(f"https://api.themoviedb.org/3/{t}/{r['id']}/external_ids?api_key={TMDB_KEY}")
        imdb = (ext.get("imdb_id") or "").strip()
        if not imdb.startswith("tt"):
            continue
        out.append({
            "id": imdb, "name": r.get("title") or r.get("name") or "", "year": (date or "")[:4],
            "description": r.get("overview") or "",
            "rating": f"{r['vote_average']:.1f}" if r.get("vote_average") else None,
            "genres": [TMDB_GENRES[g] for g in r.get("genre_ids", []) if g in TMDB_GENRES],
            "poster": f"https://image.tmdb.org/t/p/w780{r['poster_path']}" if r.get("poster_path") else None,
            "background": f"https://image.tmdb.org/t/p/w1280{r['backdrop_path']}" if r.get("backdrop_path") else None,
        })
        if len(out) >= CANDIDATES:
            break
    return out


def _trakt_rows(path: str) -> list:
    headers = {"Content-Type": "application/json", "trakt-api-version": "2", "trakt-api-key": TRAKT_ID}
    return http_json(f"https://api.trakt.tv/{path}?limit={CANDIDATES}&extended=full,images", headers)


def _trakt_items(kind: str, rows: list, count_key: str) -> list[dict]:
    out = []
    for row in rows:
        item = row.get("movie") if kind == "movie" else row.get("show")
        if not item:
            continue
        imdb = (item.get("ids") or {}).get("imdb") or ""
        if not str(imdb).startswith("tt"):
            continue
        images = item.get("images") or {}

        def first_img(key: str) -> str | None:   # Trakt returns host-relative URLs; we download and re-render them
            arr = images.get(key) or []
            return f"https://{arr[0]}" if arr and not str(arr[0]).startswith("http") else (arr[0] if arr else None)

        out.append({
            "id": imdb, "name": item.get("title") or "", "year": str(item.get("year") or ""),
            "description": item.get("overview") or "",
            "rating": f"{item['rating']:.1f}" if item.get("rating") else None,
            "genres": [g.replace("-", " ").title() for g in item.get("genres") or []],
            "poster": first_img("poster"), "background": first_img("fanart"),
            "watchers": row.get(count_key),
        })
    return out


def src_trakt(kind: str) -> list[dict]:
    """Most watched (unique users) in TRAKT_PERIOD — people who actually played it."""
    t = "movies" if kind == "movie" else "shows"
    return _trakt_items(kind, _trakt_rows(f"{t}/watched/{TRAKT_PERIOD}"), "watcher_count")


def src_trakt_trending(kind: str) -> list[dict]:
    """Trending = users watching it over the last 24 hours — the 'right now' signal."""
    t = "movies" if kind == "movie" else "shows"
    return _trakt_items(kind, _trakt_rows(f"{t}/trending"), "watchers")


SOURCES = [  # (key, label, fetcher, weight, configured?)
    ("trakt", f"Trakt most-watched ({TRAKT_PERIOD})", src_trakt, 1.0, bool(TRAKT_ID)),
    ("trakt_trending", "Trakt trending (24h)", src_trakt_trending, 0.6, bool(TRAKT_ID)),
    ("tmdb", "TMDB trending", src_tmdb, 0.8, bool(TMDB_KEY)),
    ("cinemeta", "Stremio popular", src_cinemeta, 0.7, True),
]


def consensus(kind: str) -> tuple[list[dict], list[str]]:
    """Weighted Borda-count merge of all available sources. Returns (ranked items, labels of sources that answered)."""
    tallies: dict[str, dict] = {}
    used: list[str] = []
    for key, label, fetch, weight, configured in SOURCES:
        if WANTED and key not in WANTED:
            continue
        if not configured:
            if WANTED:
                print(f"  ! {label} requested via SOURCES but its key is not set", file=sys.stderr)
            continue
        try:
            ranked = fetch(kind)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {label} unavailable: {e}", file=sys.stderr)
            continue
        if not ranked:
            print(f"  ! {label} returned nothing", file=sys.stderr)
            continue
        used.append(label)
        for pos, it in enumerate(ranked, start=1):
            t = tallies.setdefault(it["id"], {"score": 0.0, "ranks": {}, "meta": {}})
            t["score"] += (CANDIDATES - pos + 1) * weight
            t["ranks"][label] = pos
            if key == "trakt" and it.get("watchers"):
                t["meta"]["watchers"] = it["watchers"]
            for field, val in it.items():                 # first non-empty value wins, sources in priority order
                if field != "watchers" and val not in (None, "", []) and not t["meta"].get(field):
                    t["meta"][field] = val
    if len(used) > 1:
        for t in tallies.values():
            if len(t["ranks"]) == 1:
                t["score"] *= SOLO_PENALTY
    ordered = sorted(tallies.values(),
                     key=lambda t: (-t["score"], -len(t["ranks"]), min(t["ranks"].values())))
    items = []
    for t in ordered:
        m = dict(t["meta"]); m["ranks"] = t["ranks"]; m["score"] = round(t["score"], 1)
        items.append(m)
    return items, used


# ----------------------------------------------------------------------------------------------- movement
def load_state() -> dict:
    """Previous rankings, read back from the published site (dist/state.json); falls back to the live catalogs."""
    stamp = int(dt.datetime.now(dt.timezone.utc).timestamp())      # dodge the Pages CDN cache
    try:
        state = http_json(f"{SITE_URL}/state.json?t={stamp}")
        if isinstance(state.get("history"), dict):
            state.setdefault("first_seen", {})
            return state
    except Exception as e:  # noqa: BLE001
        print(f"  (no previous state: {e})", file=sys.stderr)
    state = {"history": {}, "first_seen": {}}
    yesterday = (TODAY - dt.timedelta(days=1)).isoformat()
    for kind, cat in (("movie", "top10-movies"), ("series", "top10-series")):
        try:
            metas = http_json(f"{SITE_URL}/catalog/{kind}/{cat}.json?t={stamp}").get("metas", [])
            ranks = {m["id"]: i for i, m in enumerate(metas, start=1) if m.get("id")}
            if ranks:
                state["history"][kind] = [{"date": yesterday, "ranks": ranks}]
                state["first_seen"][kind] = {i: yesterday for i in ranks}
        except Exception:  # noqa: BLE001
            pass
    return state


def baseline(state: dict, kind: str) -> dict | None:
    """The newest snapshot from a previous UTC day — what 'since yesterday' is measured against."""
    entries = [e for e in state["history"].get(kind, []) if e.get("date") != TODAY.isoformat()]
    return entries[-1] if entries else None


def movement(state: dict, kind: str, imdb: str, rank: int) -> dict:
    base = baseline(state, kind)
    first = state["first_seen"].get(kind, {}).get(imdb)
    if base is None:
        return {"new": False, "delta": None, "prev": None, "days": 1}
    prev = base["ranks"].get(imdb)
    days = (TODAY - dt.date.fromisoformat(first)).days + 1 if (prev and first) else 1
    return {"new": prev is None, "delta": (prev - rank) if prev else None, "prev": prev, "days": days}


def record_state(state: dict, kind: str, ranks: dict[str, int]) -> None:
    hist = [e for e in state["history"].get(kind, []) if e.get("date") != TODAY.isoformat()]
    hist.append({"date": TODAY.isoformat(), "ranks": ranks})
    cutoff = (TODAY - dt.timedelta(days=HISTORY_DAYS)).isoformat()
    state["history"][kind] = [e for e in hist if e.get("date", "") >= cutoff]
    base = baseline(state, kind)
    seen = state["first_seen"].setdefault(kind, {})
    for imdb in ranks:                     # keep first_seen while a title stays on the list, (re)start it otherwise
        if not (base and imdb in base["ranks"] and seen.get(imdb)):
            seen[imdb] = TODAY.isoformat()
    for imdb in list(seen):
        if imdb not in ranks:
            del seen[imdb]


def movement_text(mv: dict) -> str:
    if mv["new"]:
        return "NEW on the list today"
    parts = []
    if mv["delta"] is None or mv["delta"] == 0:
        parts.append("same spot as yesterday" if mv["prev"] else "")
    elif mv["delta"] > 0:
        parts.append(f"up {mv['delta']} since yesterday")
    else:
        parts.append(f"down {-mv['delta']} since yesterday")
    if mv["days"] > 1:
        parts.append(f"{mv['days']} days in the Top 10")
    return " · ".join(p for p in parts if p)


# ----------------------------------------------------------------------------------------------- artwork
def fetch_image(url: str | None, mode: str = "RGB") -> Image.Image | None:
    if not url:
        return None
    try:
        return Image.open(io.BytesIO(http_get(url))).convert(mode)
    except Exception as e:  # noqa: BLE001
        print(f"  ! image failed {url}: {e}", file=sys.stderr)
        return None


def best_poster(it: dict) -> Image.Image | None:
    """Highest-quality poster we can get for an IMDb id: metahub large (780x1170), then the source's own poster."""
    for url in (f"https://images.metahub.space/poster/large/{it['id']}/img", it.get("poster"),
                f"https://images.metahub.space/poster/medium/{it['id']}/img"):
        img = fetch_image(url)
        if img is not None and img.width >= 300:
            return img
    return None


def best_backdrop(it: dict) -> Image.Image | None:
    for url in (f"https://images.metahub.space/background/large/{it['id']}/img", it.get("background")):
        img = fetch_image(url)
        if img is not None and img.width >= 1000:
            return img
    return None


def title_logo(it: dict) -> Image.Image | None:
    img = fetch_image(f"https://images.metahub.space/logo/medium/{it['id']}/img", "RGBA")
    return img if img is not None and img.width >= 200 and img.getbbox() else None


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
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
    th = b - t
    x = (x0 - l) if align == "left" else (x1 - r)
    y = y1 - b
    stroke = max(4, round(th * 0.045))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((x + stroke, y + stroke * 1.5), text, font=f, fill=(0, 0, 0, 170),
                                stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(stroke * 2)))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((x, y), text, font=f, fill=NUM_FILL + (255,), stroke_width=stroke,
                               stroke_fill=NUM_STROKE + (255,))
    canvas.alpha_composite(layer)


def draw_badge(canvas: Image.Image, mv: dict, xy: tuple[int, int], size: int) -> None:
    """Top-corner pill: red NEW, green ▲n or grey ▼n. Nothing when the title held its spot."""
    if mv["new"]:
        text, color, arrow = "NEW", BADGE_NEW, None
    elif mv["delta"]:
        text, color, arrow = str(abs(mv["delta"])), (BADGE_UP if mv["delta"] > 0 else BADGE_DOWN), mv["delta"] > 0
    else:
        return
    f = ImageFont.truetype(str(FONT), size)
    l, t, r, b = f.getbbox(text)
    tw, th = r - l, b - t
    pad_x, pad_y = round(size * 0.55), round(size * 0.38)
    tri = round(size * 0.62) if arrow is not None else 0
    gap = round(size * 0.3) if arrow is not None else 0
    w, h = tw + pad_x * 2 + tri + gap, th + pad_y * 2
    x, y = xy
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((x + 3, y + 5, x + w + 3, y + h + 5), radius=round(h * 0.28), fill=(0, 0, 0, 120))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(6)))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((x, y, x + w, y + h), radius=round(h * 0.28), fill=color + (235,))
    cx = x + pad_x
    if arrow is not None:
        cy = y + h / 2
        pts = [(cx, cy + tri / 2), (cx + tri, cy + tri / 2), (cx + tri / 2, cy - tri / 2)] if arrow else \
              [(cx, cy - tri / 2), (cx + tri, cy - tri / 2), (cx + tri / 2, cy + tri / 2)]
        d.polygon(pts, fill=(255, 255, 255, 255))
        cx += tri + gap
    d.text((cx - l, y + pad_y - t), text, font=f, fill=(255, 255, 255, 255))
    canvas.alpha_composite(layer)


def gradient(w: int, h: int, direction: str, strength: int = 230) -> Image.Image:
    g = Image.new("L", (w, h), 0)
    px = g.load()
    n = w if direction == "left" else h
    for i in range(n):
        a = int(strength * (1 - i / n) ** 1.6)
        if direction == "left":
            for j in range(h):
                px[i, j] = a
        else:
            for j in range(w):
                px[j, h - 1 - i] = a
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    black.putalpha(g)
    return black


def dominant_tint(img: Image.Image) -> tuple[int, int, int]:
    r, g_, b = (int(v) for v in ImageStat.Stat(img.resize((24, 24)).convert("RGB")).mean)
    return (min(255, r + 10), min(255, g_ + 10), min(255, b + 10))


def render_poster_card(poster: Image.Image, rank: int, mv: dict) -> Image.Image:
    """2:3 card: the poster with the rank numeral in the lower-left (Netflix mobile style)."""
    card = cover(poster, POSTER_W, POSTER_H).convert("RGBA")
    card.alpha_composite(gradient(POSTER_W, POSTER_H, "bottom", 200))
    box_h = int(POSTER_H * (0.32 if rank < 10 else 0.27))
    draw_number(card, str(rank), (26, POSTER_H - 22 - box_h, int(POSTER_W * 0.80), POSTER_H - 22), "left")
    draw_badge(card, mv, (24, 24), 46)
    return card.convert("RGB")


def render_wide_card(poster: Image.Image, backdrop: Image.Image | None, logo: Image.Image | None,
                     rank: int, mv: dict) -> Image.Image:
    """16:9 card. backdrop style: the title's backdrop, big numeral left, title logo right (Netflix TV Top 10 tile).
    poster style (or fallback): dark tinted base, numeral left, poster right."""
    if WIDE_STYLE == "backdrop" and backdrop is not None and logo is not None:
        base = cover(backdrop, WIDE_W, WIDE_H).convert("RGBA")
        base.alpha_composite(Image.new("RGBA", base.size, (0, 0, 0, 60)))
        base.alpha_composite(gradient(WIDE_W, WIDE_H, "left", 235))
        base.alpha_composite(gradient(WIDE_W, WIDE_H, "bottom", 150))
        draw_number(base, str(rank), (44, int(WIDE_H * 0.20), int(WIDE_W * 0.40), WIDE_H - 30), "left")
        max_w, max_h = int(WIDE_W * 0.46), int(WIDE_H * 0.42)
        scale = min(max_w / logo.width, max_h / logo.height)
        lg = logo.resize((max(1, round(logo.width * scale)), max(1, round(logo.height * scale))), Image.LANCZOS)
        lx, ly = WIDE_W - 56 - lg.width, (WIDE_H - lg.height) // 2 + int(WIDE_H * 0.08)
        shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
        shadow.paste((0, 0, 0, 200), (lx, ly + 4, lx + lg.width, ly + 4 + lg.height), lg.split()[3])
        base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
        base.alpha_composite(lg, (lx, ly))
        draw_badge(base, mv, (40, 36), 44)
        return base.convert("RGB")
    base = Image.new("RGBA", (WIDE_W, WIDE_H), (16, 16, 16, 255))
    tint = dominant_tint(poster)
    glow = Image.new("RGBA", (WIDE_W, WIDE_H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-WIDE_W * 0.1, -WIDE_H * 0.2, WIDE_W * 1.05, WIDE_H * 1.3), fill=tint + (70,))
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(160)))
    base.alpha_composite(gradient(WIDE_W, WIDE_H, "bottom", 110))
    poster_w = int(WIDE_H * 2 / 3)
    px = WIDE_W - poster_w - 36
    p = cover(poster, poster_w, WIDE_H - 36).convert("RGBA")
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle((px - 12, 6, px + poster_w + 12, WIDE_H), fill=(0, 0, 0, 180))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(16)))
    base.alpha_composite(p, (px, 18))
    draw_number(base, str(rank), (48, int(WIDE_H * 0.14), px - 24, WIDE_H - 18), "right")
    draw_badge(base, mv, (40, 36), 44)
    return base.convert("RGB")


def render_logo() -> Image.Image:
    img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    draw_number(img, "10", (20, 150, 492, 440), "right")
    ImageDraw.Draw(img).text((26, 92), "TOP", font=ImageFont.truetype(str(FONT), 86), fill=(235, 235, 235, 255))
    return img


# ----------------------------------------------------------------------------------------------- build
def fmt_count(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else (f"{n / 1000:.0f}k" if n >= 10_000 else f"{n:,}")


def build_catalog(kind: str, state: dict) -> tuple[dict, list[str]]:
    items, used = consensus(kind)
    metas, ranks = [], {}
    for it in items:
        if len(metas) >= TOP_N:
            break
        rank = len(metas) + 1
        poster = best_poster(it)
        if poster is None:
            print(f"  - skipping {it['name']} ({it['id']}): no poster", file=sys.stderr)
            continue
        backdrop, logo = best_backdrop(it), title_logo(it)
        mv = movement(state, kind, it["id"], rank)
        tally = " · ".join(f"{src} #{pos}" for src, pos in it["ranks"].items())
        badge = "NEW" if mv["new"] else (f"{mv['delta']:+d}" if mv["delta"] else "=")
        print(f"  #{rank:<2} {badge:<4} {it['name']} ({it.get('year', '')}) {it['id']}   [{tally}]"
              f"{'' if logo is not None else '  (no logo)'}")
        card_name = f"{kind}-{rank}-{it['id']}.jpg"
        wide_name = f"{kind}-{rank}-{it['id']}-wide.jpg"
        bg_name = f"{kind}-{rank}-{it['id']}-bg.jpg"
        render_poster_card(poster, rank, mv).save(DIST / "img" / card_name, "JPEG", quality=88, optimize=True)
        render_wide_card(poster, backdrop, logo, rank, mv).save(DIST / "img" / wide_name, "JPEG", quality=88,
                                                                optimize=True)
        if backdrop is not None:
            cover(backdrop, BG_W, BG_H).save(DIST / "img" / bg_name, "JPEG", quality=82, optimize=True)
        window = {"daily": "today", "weekly": "this week", "monthly": "this month", "yearly": "this year"}
        watched = f"{fmt_count(it['watchers'])} Trakt users watched it {window[TRAKT_PERIOD]}" \
            if it.get("watchers") and TRAKT_PERIOD in window else ""
        blurb = " · ".join(p for p in (f"#{rank} in the world today", movement_text(mv), watched) if p)
        metas.append({
            "id": it["id"],
            "type": kind,
            "name": it["name"],
            "poster": f"{SITE_URL}/img/{card_name}",
            "posterShape": "poster",
            "landscapePoster": f"{SITE_URL}/img/{wide_name}",
            "background": f"{SITE_URL}/img/{bg_name if backdrop is not None else wide_name}",
            "logo": f"https://images.metahub.space/logo/medium/{it['id']}/img" if logo is not None else None,
            "description": f"{blurb} ({tally}). {it.get('description', '')}".strip(),
            "releaseInfo": it.get("year") or None,
            "imdbRating": it.get("rating"),
            "genres": it.get("genres") or [],
        })
        ranks[it["id"]] = rank
    record_state(state, kind, ranks)
    return {"metas": metas}, used


def main() -> int:
    if not SITE_URL:
        print("SITE_URL is required (e.g. https://you.github.io/nuvio-top10-addon)", file=sys.stderr)
        return 2
    for d in ("catalog/movie", "catalog/series", "img"):
        (DIST / d).mkdir(parents=True, exist_ok=True)
    for old in (DIST / "img").glob("*.jpg"):
        old.unlink()

    state = load_state()
    print("movies:")
    movies, used_m = build_catalog("movie", state)
    print("series:")
    series, used_s = build_catalog("series", state)
    if not movies["metas"] or not series["metas"]:
        print("no data from any source — keeping the previous deployment", file=sys.stderr)
        return 1
    (DIST / "catalog/movie/top10-movies.json").write_text(json.dumps(movies, ensure_ascii=False, indent=1), "utf-8")
    (DIST / "catalog/series/top10-series.json").write_text(json.dumps(series, ensure_ascii=False, indent=1), "utf-8")
    (DIST / "state.json").write_text(json.dumps(state, indent=0), "utf-8")

    used = sorted(set(used_m) | set(used_s))
    source_note = "consensus of " + " + ".join(used) if len(used) > 1 else (used[0] if used else "none")
    credits = " · ".join(c for c, on in (("Data from Trakt (trakt.tv)", any("Trakt" in u for u in used)),
                                         ("This product uses the TMDB API but is not endorsed or certified by TMDB",
                                          any("TMDB" in u for u in used)),
                                         ("Cinemeta by Stremio", any("Stremio" in u for u in used)),
                                         ("Artwork via metahub", True)) if on)
    render_logo().save(DIST / "logo.png", "PNG")
    now = dt.datetime.now(dt.timezone.utc)
    manifest = {
        "id": "community.top10today",
        "version": f"1.{now.strftime('%Y%m%d')}.{now.strftime('%H%M')}",
        "name": "Top 10 Today",
        "description": f"The {TOP_N} most-watched movies and series in the world right now, with ranked artwork, "
                       f"NEW badges and daily movement. Ranking: {source_note}. Rebuilt automatically. {credits}.",
        "logo": f"{SITE_URL}/logo.png",
        "resources": ["catalog"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": [
            {"type": "movie", "id": "top10-movies", "name": "Top 10 Movies Today"},
            {"type": "series", "id": "top10-series", "name": "Top 10 Series Today"},
        ],
        "behaviorHints": {"configurable": False, "configurationRequired": False},
    }
    (DIST / "manifest.json").write_text(json.dumps(manifest, indent=1), "utf-8")
    (DIST / ".nojekyll").write_text("")
    rows = "".join(
        f"<tr><td>{m['description'].split(' (')[0]}</td><td>{m['name']}</td></tr>"
        for m in movies["metas"] + series["metas"])
    (DIST / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Top 10 Today — Nuvio addon</title>"
        "<body style='font-family:system-ui;background:#111;color:#eee;padding:2rem;max-width:60rem'>"
        "<h1>Top 10 Today</h1><p>Install in Nuvio / Stremio with this manifest URL:</p>"
        f"<p><code style='font-size:1.1rem'>{SITE_URL}/manifest.json</code></p>"
        f"<p>Ranking: {source_note} · built {now.strftime('%Y-%m-%d %H:%M UTC')}</p>"
        "<table style='border-collapse:collapse;font-size:.95rem'>"
        f"{rows}</table>"
        "<p><a href='catalog/movie/top10-movies.json' style='color:#9cf'>movies.json</a> · "
        "<a href='catalog/series/top10-series.json' style='color:#9cf'>series.json</a> · "
        "<a href='state.json' style='color:#9cf'>state.json</a></p>"
        f"<p style='color:#888;font-size:.9rem'>{credits}.</p></body>", "utf-8")
    print(f"done -> {DIST}  ({manifest['version']})  ranking: {source_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
