# Top 10 Today — Nuvio / Stremio catalog addon

Two home-page rows, **Top 10 Movies Today** and **Top 10 Series Today**, showing what the world is watching right now
with Netflix-style ranked artwork: big outlined numerals, the title's own logo on a landscape backdrop, a red **NEW**
badge for titles that just entered the list and ▲/▼ badges for titles that moved since yesterday. The lists and the
images are rebuilt automatically every 6 hours by GitHub Actions and served from GitHub Pages — there is no server.

## Install in Nuvio

1. Open the addon page: `https://<your-github-user>.github.io/<this-repo>/` — it shows the manifest URL and today's list.
2. Nuvio → Settings → Addons → add `https://<your-github-user>.github.io/<this-repo>/manifest.json`.
3. Settings → Sources → Catalog order: drag the two Top 10 rows to the top of Home.

Each item ships three images and Nuvio picks the right one: a 2:3 poster with the numeral in the corner (poster
rows), a 16:9 tile with the numeral and the title logo on the backdrop (landscape rows), and a clean 1920×1080
backdrop for the hero / expanded-focus view. The title logo is also passed as `logo`.

## Where the "Top 10" comes from

No single site knows what the whole world is watching, so the ranking is a **consensus** of the best public signals.
Each source contributes its top 20; a title earns `(21 − rank) × weight` from every source it appears in, and the
totals decide the order. A title that is high on *several* lists beats one that is high on only one, and a title
that only one source lists (while others answered) has its score halved, so one feed's glitch can't push it in.

| Source | Measures | Weight | Needs |
|---|---|---|---|
| **Trakt — Most watched** (weekly) | unique users who actually played the title this week, worldwide | 1.0 | `TRAKT_CLIENT_ID` secret |
| **Trakt — Trending** | users watching the title in the last 24 hours | 0.6 | `TRAKT_CLIENT_ID` secret |
| **TMDB — Trending today** | worldwide activity on TMDB in the last 24 h; unreleased titles are skipped | 0.8 | `TMDB_API_KEY` secret |
| **Cinemeta — Popular** | Stremio's own popularity feed (IMDb-based) | 0.7 | nothing — always on |

Trakt carries the most weight because it is the only source that counts real viewing rather than page views.
Every item's description explains its placement, e.g.
`#2 in the world today · up 3 since yesterday · 4 days in the Top 10 · 81k Trakt users watched it this week
(Trakt most-watched (weekly) #1 · Trakt trending (24h) #4 · TMDB trending #3 · Stremio popular #6)`.

### Movement, NEW badges, days on the list

Every build writes its rankings to `state.json` next to the catalogs and reads the previous one back from the
published site, so the addon remembers up to 30 days of history without a database. Movement is measured against
the newest snapshot from a previous day ("since yesterday"); a title gets **NEW** when it was not on yesterday's
list, and "days in the Top 10" counts how long it has stayed on without dropping off.

### Turning the sources on (both free, two minutes each)

- **Trakt**: log in at trakt.tv, open https://trakt.tv/oauth/applications/new (a verified GitHub account is required),
  give the app any name, keep the **Development** app type and enter `http://localhost` as the redirect URI — the
  addon never signs anyone in, it only reads public endpoints with the Client ID, so the redirect URI is never used.
  Save, copy the **Client ID** → repo → Settings → Secrets and variables → Actions → New repository secret
  `TRAKT_CLIENT_ID`. (The "most watched" and "trending" endpoints are public on Trakt's free Standard tier.)
- **TMDB**: themoviedb.org → Settings → API → request a (free) developer key. Copy the **API Key (v3 auth)** →
  New repository secret `TMDB_API_KEY`.

Then Actions → "Build & publish Top 10 addon" → *Run workflow*. The manifest description and the addon page say
which sources went into the current build.

Optional repository **variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Default | Meaning |
|---|---|---|
| `SOURCES` | all configured | force a subset: `trakt`, `trakt_trending`, `tmdb`, `cinemeta` (comma-separated) |
| `TRAKT_PERIOD` | `weekly` | `daily`, `weekly`, `monthly` or `yearly` most-watched window |
| `WIDE_STYLE` | `backdrop` | landscape tile style: `backdrop` (backdrop + title logo) or `poster` (numeral + poster) |
| `TOP_N` | `10` | how many ranks to publish |
| `SITE_URL` | derived from the repo | only for a custom domain or a `<user>.github.io` root repo |

## Deploy your own copy

1. Create a new **public** repository on GitHub and push these files to its `main` branch.
2. Repo → Settings → Pages → Source: **GitHub Actions**.
3. Add the secrets above (optional but recommended), then Actions → "Build & publish Top 10 addon" → Run workflow.
   The site is live a minute later. The schedule lives in `.github/workflows/build.yml`.

## Run locally

```
pip install pillow
SITE_URL=https://you.github.io/nuvio-top10-addon TRAKT_CLIENT_ID=... TMDB_API_KEY=... python generate.py
```

Output lands in `dist/` (manifest.json, catalog/*.json, img/*.jpg, state.json). Without the keys it builds from
Cinemeta alone; movement is read from whatever is live at `SITE_URL`.

## Files

- `generate.py` — fetches the lists, merges them, tracks movement, renders the artwork, writes `dist/`
- `fonts/ArchivoBlack-Regular.ttf` — numeral font (SIL Open Font License, from Google Fonts)
- `.github/workflows/build.yml` — scheduled build + Pages deployment

Artwork (posters, backdrops, title logos) comes from metahub, the same image service Stremio's Cinemeta uses.
