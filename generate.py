#!/usr/bin/env python3
"""Top 10 Today — a static Stremio/Nuvio catalog addon with Netflix-style ranking artwork.

Builds `dist/` (manifest + catalogs + numbered images + state). Hosted on GitHub Pages and rebuilt on a schedule,
so the lists and artwork stay current without a server.

Rows (ROWS env, default "global,region"):
  global    Top 10 Movies / Series Today — consensus of every configured source (weighted Borda count).
            Each source contributes its top 40; an item scores (41 - rank) x weight per source, totals are sorted.
            A title that only one source lists (while several answered) is halved, so one feed's glitch can't push it in.
              * Trakt    "most watched" (unique users who actually played it, weekly)   weight 1.0  TRAKT_CLIENT_ID
              * Trakt    "trending" (users watching it in the last 24 h)                 weight 0.6  TRAKT_CLIENT_ID
              * TMDB     "trending today" (worldwide TMDB activity, released titles only) weight 0.8 TMDB_API_KEY
              * Cinemeta "popular" (Stremio's popularity feed, IMDb-based)               weight 0.7  always on
  region    Top 10 Movies / Series in <REGION> — the same consensus ranking, kept to titles that are actually
            streamable (subscription / free / ad-supported) in REGION according to TMDB's JustWatch-sourced
            availability data; topped up from TMDB's popular-on-streaming list if the consensus pool runs short.
            Needs TMDB_API_KEY.
  trending  Trending Right Now — Trakt's 24-hour "being watched" list, as is. Needs TRAKT_CLIENT_ID.

Every title is enriched from Cinemeta's meta (the canonical IMDb record): real IMDb rating, genres, runtime,
cast, director, trailers. Previous rankings live in dist/state.json on the published site, so descriptions can
say how a title moved since yesterday and how long it has been on the list.

Environment:
  SITE_URL          public base URL of the deployed site, e.g. https://you.github.io/nuvio-top10-addon  (required)
  TRAKT_CLIENT_ID   optional Trakt API app client id (free: trakt.tv/oauth/applications/new)
  TMDB_API_KEY      optional TMDB v3 API key (free: themoviedb.org/settings/api)
  REGION            ISO country code for the regional rows (default US)
  ROWS              comma list of row sets to publish: global, region, trending (default global,region)
  TRAKT_PERIOD      daily | weekly | monthly | yearly  (default weekly)
  TOP_N             how many ranks to publish (default 10)
  SOURCES           comma list to force a subset: trakt,trakt_trending,tmdb,cinemeta (default: all configured)
  POSTER_STYLE      overlay (numeral on the full-bleed poster, default) | merged (poster top-right, numeral under it)
  WIDE_STYLE        poster (numeral beside the poster, default) | merged | backdrop (backdrop + title logo)
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

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
FONT = ROOT / "fonts" / "ArchivoBlack-Regular.ttf"
SITE_URL = os.environ.get("SITE_URL", "").strip().rstrip("/")
TMDB_KEY = os.environ.get("TMDB_API_KEY", "").strip()
TRAKT_ID = os.environ.get("TRAKT_CLIENT_ID", "").strip()
TRAKT_PERIOD = os.environ.get("TRAKT_PERIOD", "weekly").strip().lower() or "weekly"
REGION = (os.environ.get("REGION", "US").strip().upper() or "US")[:2]
ROWS = {s.strip().lower() for s in (os.environ.get("ROWS") or "global,region").split(",") if s.strip()}
TOP_N = int(os.environ.get("TOP_N", "10"))
WANTED = {s.strip().lower() for s in os.environ.get("SOURCES", "").split(",") if s.strip()}
POSTER_STYLE = os.environ.get("POSTER_STYLE", "overlay").strip().lower() or "overlay"
WIDE_STYLE = os.environ.get("WIDE_STYLE", "poster").strip().lower() or "poster"
CANDIDATES = 40                        # ranks taken from each source before the consensus vote
SOLO_PENALTY = 0.5                     # score multiplier for a title only one source lists (when >1 answered)
HISTORY_DAYS = 30
TODAY = dt.datetime.now(dt.timezone.utc).date()
UA = "nuvio-top10-addon/4.0 (+https://github.com)"
TMDB = "https://api.themoviedb.org/3"
CINEMETA = "https://v3-cinemeta.strem.io"

POSTER_W, POSTER_H = 600, 900          # 2:3 card
WIDE_W, WIDE_H = 1280, 720             # 16:9 card
BG_W, BG_H = 1920, 1080                # plain backdrop (hero / expanded focus)
NUM_FILL = (24, 24, 24)
NUM_STROKE = (178, 178, 178)

REGION_NAMES = {
    "US": "the U.S.", "GB": "the U.K.", "CA": "Canada", "AU": "Australia", "NZ": "New Zealand", "IE": "Ireland",
    "DE": "Germany", "FR": "France", "ES": "Spain", "IT": "Italy", "NL": "the Netherlands", "BE": "Belgium",
    "PT": "Portugal", "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland", "PL": "Poland",
    "AT": "Austria", "CH": "Switzerland", "TR": "Türkiye", "GR": "Greece", "CZ": "Czechia", "HU": "Hungary",
    "RO": "Romania", "BR": "Brazil", "MX": "Mexico", "AR": "Argentina", "CL": "Chile", "CO": "Colombia",
    "IN": "India", "JP": "Japan", "KR": "South Korea", "PH": "the Philippines", "ID": "Indonesia", "TH": "Thailand",
    "MY": "Malaysia", "SG": "Singapore", "ZA": "South Africa", "NG": "Nigeria", "KE": "Kenya", "UG": "Uganda",
    "GH": "Ghana", "EG": "Egypt", "AE": "the UAE", "SA": "Saudi Arabia", "IL": "Israel",
}
REGION_NAME = REGION_NAMES.get(REGION, REGION)

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


def tmdb_url(path: str, **params) -> str:
    q = {"api_key": TMDB_KEY, **{k: v for k, v in params.items() if v is not None}}
    return f"{TMDB}/{path}?{urllib.parse.urlencode(q)}"


# ----------------------------------------------------------------------------------------------- sources
# Every source returns an ordered list of candidate dicts:
#   {"id": "tt...", "name", "year", "description", "rating", "genres", "poster", "background", ["watchers"], ["tmdb"]}
def src_cinemeta(kind: str) -> list[dict]:
    typ = "movie" if kind == "movie" else "series"
    data = http_json(f"{CINEMETA}/catalog/{typ}/top.json")
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


def _tmdb_items(kind: str, path: str, limit: int, **params) -> list[dict]:
    """Walk a paged TMDB list (trending / discover), keeping released, non-adult titles that have an IMDb id."""
    t = "movie" if kind == "movie" else "tv"
    out, seen = [], set()
    for page in range(1, 8):                                   # 20 results per page
        data = http_json(tmdb_url(path, language="en-US", page=page, **params))
        results = data.get("results", [])
        for r in results:
            date = (r.get("release_date") if t == "movie" else r.get("first_air_date")) or ""
            # "trending" counts page views, so an unreleased title with a hot trailer can sit at #1 even though
            # nobody can watch it yet. A Top 10 of what the world is watching should only hold released titles.
            if r.get("adult") or not date or date > TODAY.isoformat():
                continue
            ext = http_json(tmdb_url(f"{t}/{r['id']}/external_ids"))
            imdb = (ext.get("imdb_id") or "").strip()
            if not imdb.startswith("tt") or imdb in seen:
                continue
            seen.add(imdb)
            out.append({
                "id": imdb, "tmdb": r["id"], "name": r.get("title") or r.get("name") or "", "year": date[:4],
                "description": r.get("overview") or "",
                "rating": f"{r['vote_average']:.1f}" if r.get("vote_average") else None,
                "genres": [TMDB_GENRES[g] for g in r.get("genre_ids", []) if g in TMDB_GENRES],
                "poster": f"https://image.tmdb.org/t/p/w780{r['poster_path']}" if r.get("poster_path") else None,
                "background": f"https://image.tmdb.org/t/p/w1280{r['backdrop_path']}" if r.get("backdrop_path") else None,
            })
            if len(out) >= limit:
                return out
        if len(results) < 20 or page >= int(data.get("total_pages") or 1):
            break
    return out


def src_tmdb(kind: str) -> list[dict]:
    t = "movie" if kind == "movie" else "tv"
    return _tmdb_items(kind, f"trending/{t}/day", CANDIDATES)


def src_tmdb_region(kind: str, limit: int) -> list[dict]:
    """TMDB's most popular titles that are streamable in REGION (subscription, free or ad-supported)."""
    if kind == "movie":
        return _tmdb_items(kind, "discover/movie", limit, sort_by="popularity.desc", include_adult="false",
                           watch_region=REGION, with_watch_monetization_types="flatrate|free|ads",
                           **{"primary_release_date.lte": TODAY.isoformat(), "vote_count.gte": 20})
    return _tmdb_items(kind, "discover/tv", limit, sort_by="popularity.desc", include_adult="false",
                       include_null_first_air_dates="false", watch_region=REGION,
                       with_watch_monetization_types="flatrate|free|ads",
                       **{"first_air_date.lte": TODAY.isoformat(), "vote_count.gte": 20})


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
            "id": imdb, "tmdb": (item.get("ids") or {}).get("tmdb"), "name": item.get("title") or "",
            "year": str(item.get("year") or ""), "description": item.get("overview") or "",
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
_source_cache: dict[tuple[str, str], list[dict]] = {}


def fetch_source(key: str, kind: str) -> list[dict]:
    if (key, kind) not in _source_cache:
        fetcher = next(f for k, _, f, _, _ in SOURCES if k == key)
        _source_cache[(key, kind)] = fetcher(kind)
    return _source_cache[(key, kind)]


def consensus(kind: str) -> tuple[list[dict], list[str]]:
    """Weighted Borda-count merge of all available sources. Returns (ranked items, labels of sources that answered)."""
    tallies: dict[str, dict] = {}
    used: list[str] = []
    for key, label, _fetch, weight, configured in SOURCES:
        if WANTED and key not in WANTED:
            continue
        if not configured:
            if WANTED:
                print(f"  ! {label} requested via SOURCES but its key is not set", file=sys.stderr)
            continue
        try:
            ranked = fetch_source(key, kind)
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


# ----------------------------------------------------------------------------------------------- enrichment
_meta_cache: dict[str, dict] = {}


def enrich(kind: str, it: dict) -> dict:
    """Overlay Cinemeta's canonical IMDb record (rating, genres, runtime, cast, trailers, TMDB id) on a candidate."""
    if it.get("_enriched"):
        return it
    typ = "movie" if kind == "movie" else "series"
    if it["id"] not in _meta_cache:
        try:
            _meta_cache[it["id"]] = http_json(f"{CINEMETA}/meta/{typ}/{it['id']}.json").get("meta") or {}
        except Exception as e:  # noqa: BLE001
            print(f"  ! cinemeta meta {it['id']}: {e}", file=sys.stderr)
            _meta_cache[it["id"]] = {}
    m = _meta_cache[it["id"]]
    if m:
        it["name"] = m.get("name") or it.get("name")
        it["rating"] = str(m["imdbRating"]) if m.get("imdbRating") else it.get("rating")
        it["genres"] = m.get("genres") or m.get("genre") or it.get("genres") or []
        it["description"] = m.get("description") or it.get("description") or ""
        it["year"] = str(m.get("releaseInfo") or m.get("year") or it.get("year") or "")
        it["tmdb"] = it.get("tmdb") or m.get("moviedb_id")
        for f in ("runtime", "released", "director", "writer", "cast", "trailers", "trailerStreams", "status"):
            if m.get(f):
                it[f] = m[f]
    it["_enriched"] = True
    return it


_providers_cache: dict[tuple[str, int], list[str]] = {}


def providers(kind: str, it: dict) -> list[str] | None:
    """Streaming services carrying the title in REGION (subscription / free / ads), None when unknown."""
    tmdb_id = it.get("tmdb")
    if not tmdb_id and TMDB_KEY:
        try:
            found = http_json(tmdb_url(f"find/{it['id']}", external_source="imdb_id"))
            hits = found.get("movie_results" if kind == "movie" else "tv_results") or []
            tmdb_id = it["tmdb"] = hits[0]["id"] if hits else None
        except Exception as e:  # noqa: BLE001
            print(f"  ! tmdb find {it['id']}: {e}", file=sys.stderr)
    if not tmdb_id:
        return None
    key = (kind, int(tmdb_id))
    if key not in _providers_cache:
        t = "movie" if kind == "movie" else "tv"
        try:
            data = http_json(tmdb_url(f"{t}/{tmdb_id}/watch/providers"))
        except Exception as e:  # noqa: BLE001
            print(f"  ! tmdb providers {it['id']}: {e}", file=sys.stderr)
            return None
        region = (data.get("results") or {}).get(REGION) or {}
        names: list[str] = []
        for bucket in ("flatrate", "free", "ads"):
            for p in region.get(bucket) or []:
                n = service_name(p.get("provider_name") or "")
                if n and n not in names:
                    names.append(n)
        _providers_cache[key] = names
    return _providers_cache[key]


TIER_SUFFIXES = (" Amazon Channel", " Apple TV Channel", " Roku Premium Channel", " Standard with Ads",
                 " Free with Ads", " with Ads", " Premium Plus", " Essential", " Premium", " Basic")


def service_name(name: str) -> str:
    """'Peacock Premium Plus' -> 'Peacock', 'Netflix Standard with Ads' -> 'Netflix', 'HBO Max Amazon Channel' -> 'HBO Max'."""
    name = name.strip()
    changed = True
    while changed:
        changed = False
        for suf in TIER_SUFFIXES:
            if name.endswith(suf) and len(name) > len(suf):
                name = name[: -len(suf)].strip()
                changed = True
    return name


def regional(kind: str, pool: list[dict]) -> list[dict]:
    """The consensus ranking kept to titles streamable in REGION; topped up from TMDB's popular-on-streaming list."""
    out, seen = [], set()
    for it in pool:
        if len(out) >= TOP_N:
            break
        enrich(kind, it)
        names = providers(kind, it)
        if names:
            it = dict(it); it["providers"] = names
            out.append(it); seen.add(it["id"])
    if len(out) < TOP_N:
        try:
            for it in src_tmdb_region(kind, TOP_N * 3):
                if len(out) >= TOP_N:
                    break
                if it["id"] in seen:
                    continue
                enrich(kind, it)
                names = providers(kind, it)
                if names:
                    it["providers"] = names; it["ranks"] = {}
                    out.append(it); seen.add(it["id"])
        except Exception as e:  # noqa: BLE001
            print(f"  ! TMDB regional top-up unavailable: {e}", file=sys.stderr)
    return out


# ----------------------------------------------------------------------------------------------- movement
def load_state() -> dict:
    """Previous rankings, read back from the published site (dist/state.json); falls back to the live catalogs."""
    stamp = int(dt.datetime.now(dt.timezone.utc).timestamp())      # dodge the Pages CDN cache
    state = {"history": {}, "first_seen": {}}
    try:
        prev = http_json(f"{SITE_URL}/state.json?t={stamp}")
        if isinstance(prev.get("history"), dict):
            state = prev
            state.setdefault("first_seen", {})
    except Exception as e:  # noqa: BLE001
        print(f"  (no previous state: {e})", file=sys.stderr)
    for old, new in (("movie", "top10-movies"), ("series", "top10-series")):   # keys from earlier versions
        for part in ("history", "first_seen"):
            if old in state[part] and new not in state[part]:
                state[part][new] = state[part].pop(old)
            state[part].pop(old, None)
    if not state["history"]:
        yesterday = (TODAY - dt.timedelta(days=1)).isoformat()
        for kind, cat in (("movie", "top10-movies"), ("series", "top10-series")):
            try:
                metas = http_json(f"{SITE_URL}/catalog/{kind}/{cat}.json?t={stamp}").get("metas", [])
                ranks = {m["id"]: i for i, m in enumerate(metas, start=1) if m.get("id")}
                if ranks:
                    state["history"][cat] = [{"date": yesterday, "ranks": ranks}]
                    state["first_seen"][cat] = {i: yesterday for i in ranks}
            except Exception:  # noqa: BLE001
                pass
    return state


def baseline(state: dict, cat: str) -> dict | None:
    """The newest snapshot from a previous UTC day — what 'since yesterday' is measured against."""
    entries = [e for e in state["history"].get(cat, []) if e.get("date") != TODAY.isoformat()]
    return entries[-1] if entries else None


def movement(state: dict, cat: str, imdb: str, rank: int) -> dict:
    base = baseline(state, cat)
    first = state["first_seen"].get(cat, {}).get(imdb)
    if base is None:
        return {"new": False, "delta": None, "prev": None, "days": 1}
    prev = base["ranks"].get(imdb)
    days = (TODAY - dt.date.fromisoformat(first)).days + 1 if (prev and first) else 1
    return {"new": prev is None, "delta": (prev - rank) if prev else None, "prev": prev, "days": days}


def record_state(state: dict, cat: str, ranks: dict[str, int]) -> None:
    hist = [e for e in state["history"].get(cat, []) if e.get("date") != TODAY.isoformat()]
    hist.append({"date": TODAY.isoformat(), "ranks": ranks})
    cutoff = (TODAY - dt.timedelta(days=HISTORY_DAYS)).isoformat()
    state["history"][cat] = [e for e in hist if e.get("date", "") >= cutoff]
    base = baseline(state, cat)
    seen = state["first_seen"].setdefault(cat, {})
    for imdb in ranks:                     # keep first_seen while a title stays on the list, (re)start it otherwise
        if not (base and imdb in base["ranks"] and seen.get(imdb)):
            seen[imdb] = TODAY.isoformat()
    for imdb in list(seen):
        if imdb not in ranks:
            del seen[imdb]


def movement_text(mv: dict) -> str:
    if mv["new"]:
        return "new on the list today"
    parts = []
    if mv["delta"] is None or mv["delta"] == 0:
        parts.append("same spot as yesterday" if mv["prev"] else "")
    elif mv["delta"] > 0:
        parts.append(f"up {mv['delta']} since yesterday")
    else:
        parts.append(f"down {-mv['delta']} since yesterday")
    if mv["days"] > 1:
        parts.append(f"{mv['days']} days on the list")
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


_art_cache: dict[tuple[str, str], Image.Image | None] = {}


def art(kind_of: str, it: dict) -> Image.Image | None:
    key = (kind_of, it["id"])
    if key not in _art_cache:
        _art_cache[key] = {"poster": best_poster, "backdrop": best_backdrop, "logo": title_logo}[kind_of](it)
    return _art_cache[key]


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


def draw_number(canvas: Image.Image, text: str, box: tuple[int, int, int, int], align: str,
                font: ImageFont.FreeTypeFont | None = None) -> None:
    """Netflix-style numeral: dark fill, grey outline, soft shadow. box=(x0,y0,x1,y1); align='left'|'right'."""
    x0, y0, x1, y1 = box
    f = font or font_for(text, x1 - x0, y1 - y0)
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


def dark_base(poster: Image.Image, backdrop: Image.Image | None, w: int, h: int) -> Image.Image:
    """Near-black base with the title's own blurred backdrop showing through faintly, plus a poster-tinted glow."""
    base = Image.new("RGBA", (w, h), (14, 14, 14, 255))
    if backdrop is not None:
        bg = cover(backdrop, w, h).filter(ImageFilter.GaussianBlur(28))
        base.alpha_composite(Image.eval(bg, lambda v: int(v * 0.28)).convert("RGBA"))
    tint = dominant_tint(poster)
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((w * 0.25, -h * 0.3, w * 1.1, h * 1.2), fill=tint + (60,))
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(140)))
    base.alpha_composite(gradient(w, h, "bottom", 120))
    return base


def paste_poster(base: Image.Image, poster: Image.Image, x: int, y: int, w: int, h: int) -> None:
    p = cover(poster, w, h).convert("RGBA")
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle((x - 14, y - 6, x + w + 14, y + h + 16), fill=(0, 0, 0, 200))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))
    base.alpha_composite(p, (x, y))


def render_poster_card(poster: Image.Image, rank: int) -> Image.Image:
    """2:3 card.
    overlay (default): full-bleed poster with the numeral drawn over its lower-left corner (Netflix mobile style).
    merged           : poster top-right, big numeral bottom-left with the poster's lower corner overlapping it."""
    text = str(rank)
    if POSTER_STYLE != "merged":
        card = cover(poster, POSTER_W, POSTER_H).convert("RGBA")
        card.alpha_composite(gradient(POSTER_W, POSTER_H, "bottom", 200))
        box_h = int(POSTER_H * (0.32 if rank < 10 else 0.27))
        draw_number(card, text, (26, POSTER_H - 22 - box_h, int(POSTER_W * 0.80), POSTER_H - 22), "left")
        return card.convert("RGB")
    base = Image.new("RGBA", (POSTER_W, POSTER_H), (12, 12, 12, 255))
    tint = dominant_tint(poster)
    glow = Image.new("RGBA", (POSTER_W, POSTER_H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((0, -POSTER_H * 0.1, POSTER_W * 1.3, POSTER_H * 0.9), fill=tint + (60,))
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120)))
    pad = 18
    ph = round(POSTER_H * 0.74)
    pw = round(ph * 2 / 3)
    px, py = POSTER_W - pad - pw, pad
    f = ImageFont.truetype(str(FONT), round(POSTER_H * 0.42 / 0.72))
    l, t, r, b = f.getbbox(text)
    while pad + (r - l) > POSTER_W - pad:
        f = ImageFont.truetype(str(FONT), f.size - 8)
        l, t, r, b = f.getbbox(text)
    nw, nh = r - l, b - t
    bottom = POSTER_H - pad - 4
    draw_number(base, text, (pad, bottom - nh, pad + nw, bottom), "left", font=f)
    paste_poster(base, poster, px, py, pw, ph)
    return base.convert("RGB")


def render_wide_card(poster: Image.Image, backdrop: Image.Image | None, logo: Image.Image | None,
                     rank: int) -> Image.Image:
    """16:9 card.
    poster (default): dark tinted base, numeral left, poster right.
    merged          : big numeral with its right part tucked behind the poster, the pair centred.
    backdrop        : the title's backdrop, numeral left, title logo right (needs a logo; else poster style)."""
    text = str(rank)
    if WIDE_STYLE == "backdrop" and backdrop is not None and logo is not None:
        base = cover(backdrop, WIDE_W, WIDE_H).convert("RGBA")
        base.alpha_composite(Image.new("RGBA", base.size, (0, 0, 0, 60)))
        base.alpha_composite(gradient(WIDE_W, WIDE_H, "left", 235))
        base.alpha_composite(gradient(WIDE_W, WIDE_H, "bottom", 150))
        draw_number(base, text, (44, int(WIDE_H * 0.20), int(WIDE_W * 0.40), WIDE_H - 30), "left")
        max_w, max_h = int(WIDE_W * 0.46), int(WIDE_H * 0.42)
        scale = min(max_w / logo.width, max_h / logo.height)
        lg = logo.resize((max(1, round(logo.width * scale)), max(1, round(logo.height * scale))), Image.LANCZOS)
        lx, ly = WIDE_W - 56 - lg.width, (WIDE_H - lg.height) // 2 + int(WIDE_H * 0.08)
        shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
        shadow.paste((0, 0, 0, 200), (lx, ly + 4, lx + lg.width, ly + 4 + lg.height), lg.split()[3])
        base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
        base.alpha_composite(lg, (lx, ly))
        return base.convert("RGB")
    if WIDE_STYLE == "merged":
        base = dark_base(poster, backdrop, WIDE_W, WIDE_H)
        pad = 26
        ph = WIDE_H - pad * 2
        pw = round(ph * 2 / 3)
        f = ImageFont.truetype(str(FONT), int(WIDE_H * 1.02))
        l, t, r, b = f.getbbox(text)
        nw = r - l
        hide = 0.42 if len(text) == 1 else 0.26
        overlap = round(nw * hide)
        while nw - overlap + pw > WIDE_W - 2 * pad:
            f = ImageFont.truetype(str(FONT), f.size - 8)
            l, t, r, b = f.getbbox(text)
            nw = r - l
            overlap = round(nw * hide)
        nh = b - t
        gx = (WIDE_W - (nw - overlap + pw)) // 2
        draw_number(base, text, (gx, WIDE_H - pad - 10 - nh, gx + nw, WIDE_H - pad - 10), "left", font=f)
        paste_poster(base, poster, gx + nw - overlap, pad, pw, ph)
        return base.convert("RGB")
    base = dark_base(poster, None, WIDE_W, WIDE_H)
    poster_w = int(WIDE_H * 2 / 3)
    px = WIDE_W - poster_w - 36
    paste_poster(base, poster, px, 18, poster_w, WIDE_H - 36)
    draw_number(base, text, (48, int(WIDE_H * 0.14), px - 24, WIDE_H - 18), "right")
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


def blurb_for(row: str, rank: int, it: dict, mv: dict) -> str:
    window = {"daily": "today", "weekly": "this week", "monthly": "this month", "yearly": "this year"}
    if row == "global":
        head = f"#{rank} in the world today"
        watched = f"{fmt_count(it['watchers'])} Trakt users watched it {window[TRAKT_PERIOD]}" \
            if it.get("watchers") and TRAKT_PERIOD in window else ""
    elif row == "region":
        head = f"#{rank} on streaming in {REGION_NAME} today"
        watched = "streaming on " + ", ".join(it.get("providers", [])[:4]) if it.get("providers") else ""
    else:
        head = f"#{rank} trending on Trakt right now"
        watched = f"{fmt_count(it['watchers'])} people watching" if it.get("watchers") else ""
    return " · ".join(p for p in (head, movement_text(mv), watched) if p)


def build_row(row: str, kind: str, cat: str, items: list[dict], state: dict) -> tuple[dict, list[str]]:
    """Render artwork + metas for one row. Returns (catalog, one-line blurbs for the addon page)."""
    metas, ranks, blurbs = [], {}, []
    for it in items:
        if len(metas) >= TOP_N:
            break
        rank = len(metas) + 1
        enrich(kind, it)
        poster = art("poster", it)
        if poster is None:
            print(f"  - skipping {it['name']} ({it['id']}): no poster", file=sys.stderr)
            continue
        backdrop, logo = art("backdrop", it), art("logo", it)
        mv = movement(state, cat, it["id"], rank)
        tally = " · ".join(f"{src} #{pos}" for src, pos in (it.get("ranks") or {}).items())
        extra = f"  [{tally}]" if tally else ""
        if row == "region":
            extra += "  {" + ", ".join(it.get("providers", [])[:3]) + "}"
        print(f"  #{rank:<2} {it['name']} ({it.get('year', '')}) {it['id']}  IMDb {it.get('rating') or '-'}{extra}")
        stem = f"{cat}-{rank}-{it['id']}"
        render_poster_card(poster, rank).save(DIST / "img" / f"{stem}.jpg", "JPEG", quality=88, optimize=True)
        render_wide_card(poster, backdrop, logo, rank).save(DIST / "img" / f"{stem}-wide.jpg", "JPEG",
                                                            quality=88, optimize=True)
        bg_path = DIST / "img" / f"{kind}-{it['id']}-bg.jpg"               # rank-independent, shared between rows
        if backdrop is not None and not bg_path.exists():
            cover(backdrop, BG_W, BG_H).save(bg_path, "JPEG", quality=82, optimize=True)
        blurb = blurb_for(row, rank, it, mv)
        meta = {
            "id": it["id"],
            "type": kind,
            "name": it["name"],
            "poster": f"{SITE_URL}/img/{stem}.jpg",
            "posterShape": "poster",
            "landscapePoster": f"{SITE_URL}/img/{stem}-wide.jpg",
            "background": f"{SITE_URL}/img/{bg_path.name if backdrop is not None else stem + '-wide.jpg'}",
            "logo": f"https://images.metahub.space/logo/medium/{it['id']}/img" if logo is not None else None,
            "description": f"{blurb}{' (' + tally + ')' if tally else ''}. {it.get('description', '')}".strip(),
            "releaseInfo": it.get("year") or None,
            "imdbRating": it.get("rating"),
            "genres": it.get("genres") or [],
        }
        for f in ("runtime", "released", "director", "writer", "cast", "trailers", "trailerStreams"):
            if it.get(f):
                meta[f] = it[f]
        metas.append(meta)
        blurbs.append(blurb)
        ranks[it["id"]] = rank
    record_state(state, cat, ranks)
    return {"metas": metas}, blurbs


def main() -> int:
    if not SITE_URL:
        print("SITE_URL is required (e.g. https://you.github.io/nuvio-top10-addon)", file=sys.stderr)
        return 2
    for d in ("catalog/movie", "catalog/series", "img"):
        (DIST / d).mkdir(parents=True, exist_ok=True)
    for old in (DIST / "img").glob("*.jpg"):
        old.unlink()
    for old in list((DIST / "catalog/movie").glob("*.json")) + list((DIST / "catalog/series").glob("*.json")):
        old.unlink()

    state = load_state()
    catalogs, used_all, page_rows = [], set(), []
    rg = REGION.lower()
    for kind, plural in (("movie", "Movies"), ("series", "Series")):
        pool, used = consensus(kind)
        used_all.update(used)
        plan = []
        if "global" in ROWS:
            plan.append(("global", f"top10-{kind if kind == 'series' else 'movies'}", f"Top 10 {plural} Today", pool))
        if "region" in ROWS:
            if TMDB_KEY:
                plan.append(("region", f"top10-{'movies' if kind == 'movie' else 'series'}-{rg}",
                             f"Top 10 {plural} in {REGION_NAME}", None))
            else:
                print("  ! regional rows need TMDB_API_KEY — skipped", file=sys.stderr)
        if "trending" in ROWS:
            if TRAKT_ID:
                plan.append(("trending", f"trending-{'movies' if kind == 'movie' else 'series'}",
                             f"Trending Right Now — {plural}", None))
            else:
                print("  ! trending rows need TRAKT_CLIENT_ID — skipped", file=sys.stderr)
        for row, cat, name, items in plan:
            print(f"{name}:")
            try:
                if items is None:
                    items = regional(kind, pool) if row == "region" else fetch_source("trakt_trending", kind)
            except Exception as e:  # noqa: BLE001
                print(f"  ! {name} unavailable: {e}", file=sys.stderr)
                continue
            catalog, blurbs = build_row(row, kind, cat, items, state)
            if not catalog["metas"]:
                print(f"  ! {name}: nothing to publish", file=sys.stderr)
                continue
            (DIST / f"catalog/{kind}/{cat}.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=1), "utf-8")
            catalogs.append({"type": kind, "id": cat, "name": name})
            page_rows.append((name, list(zip(catalog["metas"], blurbs))))
    if not any(c["id"].startswith("top10-") for c in catalogs):
        print("no data from any source — keeping the previous deployment", file=sys.stderr)
        return 1
    order = {"top10-movies": 0, "top10-series": 1}                       # global rows first, then region, trending
    catalogs.sort(key=lambda c: (order.get(c["id"], 2 if "-" + rg in c["id"] else 3), c["type"] != "movie"))
    (DIST / "state.json").write_text(json.dumps(state, indent=0), "utf-8")

    used = sorted(used_all)
    source_note = "consensus of " + " + ".join(used) if len(used) > 1 else (used[0] if used else "none")
    credits = " · ".join(c for c, on in (("Data from Trakt (trakt.tv)", any("Trakt" in u for u in used)),
                                         ("This product uses the TMDB API but is not endorsed or certified by TMDB",
                                          bool(TMDB_KEY)),
                                         ("Metadata and artwork via Cinemeta / metahub (Stremio)", True)) if on)
    render_logo().save(DIST / "logo.png", "PNG")
    now = dt.datetime.now(dt.timezone.utc)
    manifest = {
        "id": "community.top10today",
        "version": f"1.{now.strftime('%Y%m%d')}.{now.strftime('%H%M')}",
        "name": "Top 10 Today",
        "description": f"The {TOP_N} most-watched movies and series in the world right now"
                       f"{' and on streaming in ' + REGION_NAME if any('-' + rg in c['id'] for c in catalogs) else ''}"
                       f", with ranked artwork. Ranking: {source_note}. Rebuilt automatically. {credits}.",
        "logo": f"{SITE_URL}/logo.png",
        "resources": ["catalog"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": catalogs,
        "behaviorHints": {"configurable": False, "configurationRequired": False},
    }
    (DIST / "manifest.json").write_text(json.dumps(manifest, indent=1), "utf-8")
    (DIST / ".nojekyll").write_text("")
    sections = ""
    for name, entries in page_rows:
        rows_html = "".join(
            f"<tr><td style='color:#888'>{i}</td><td>{m['name']} <span style='color:#777'>({m.get('releaseInfo') or ''})</span></td>"
            f"<td style='color:#9c6'>{m.get('imdbRating') or ''}</td><td style='color:#aaa'>{blurb}</td></tr>"
            for i, (m, blurb) in enumerate(entries, start=1))
        sections += f"<h2 style='margin-top:2rem;font-size:1.1rem'>{name}</h2><table style='border-collapse:collapse;font-size:.9rem'>{rows_html}</table>"
    (DIST / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Top 10 Today — Nuvio addon</title>"
        "<style>td{padding:.25rem .6rem;vertical-align:top;border-bottom:1px solid #222}</style>"
        "<body style='font-family:system-ui;background:#111;color:#eee;padding:2rem;max-width:70rem'>"
        "<h1>Top 10 Today</h1><p>Install in Nuvio / Stremio with this manifest URL:</p>"
        f"<p><code style='font-size:1.1rem'>{SITE_URL}/manifest.json</code></p>"
        f"<p>Ranking: {source_note} · built {now.strftime('%Y-%m-%d %H:%M UTC')}</p>"
        f"{sections}"
        "<p style='margin-top:2rem'>" + " · ".join(
            f"<a href='catalog/{c['type']}/{c['id']}.json' style='color:#9cf'>{c['id']}.json</a>" for c in catalogs)
        + " · <a href='state.json' style='color:#9cf'>state.json</a></p>"
        f"<p style='color:#888;font-size:.9rem'>{credits}.</p></body>", "utf-8")
    print(f"done -> {DIST}  ({manifest['version']})  rows: {', '.join(c['id'] for c in catalogs)}  ranking: {source_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
