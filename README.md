# Top 10 Today — Nuvio / Stremio catalog addon

Two home-page rows, **Top 10 Movies Today** and **Top 10 Series Today**, showing what the world is watching right now
with Netflix-style ranked artwork (big outlined numerals next to the poster). The lists and the images are rebuilt
automatically every 6 hours by GitHub Actions and served from GitHub Pages — there is no server to run.

## Install in Nuvio

1. Open the addon page: `https://<your-github-user>.github.io/<this-repo>/` — it shows the manifest URL.
2. Nuvio → Settings → Addons → add `https://<your-github-user>.github.io/<this-repo>/manifest.json`.
3. Settings → Sources → Catalog order: drag the two Top 10 rows to the top of Home.

The artwork comes in two shapes and Nuvio picks the right one automatically: a 2:3 poster with the numeral in the
corner for poster rows, and a 16:9 tile with the numeral beside the poster for landscape rows.

## Where the "Top 10" comes from

No single site knows what the whole world is watching, so the ranking is a **consensus** of the best public signals.
Each source contributes its top 20; a title earns `(21 − rank) × weight` from every source it appears in, and the
totals decide the order. A title that is high on *several* lists beats one that is high on only one, and a title
that one site ranks oddly (a glitch, an unreleased film with a hot trailer) can't take the #1 spot on its own.

| Source | Measures | Weight | Needs |
|---|---|---|---|
| **Trakt — Most watched** (weekly) | unique users who actually played the title this week, worldwide | 1.0 | `TRAKT_CLIENT_ID` secret |
| **TMDB — Trending today** | worldwide activity on TMDB in the last 24 h; unreleased titles are skipped | 0.8 | `TMDB_API_KEY` secret |
| **Cinemeta — Popular** | Stremio's own popularity feed (IMDb-based) | 0.7 | nothing — always on |

Trakt is the strongest signal because it is the only one that counts real viewing rather than page views, which is
why it carries the most weight. Every item's description lists its rank on each source
(`#1 in the world today (Trakt most-watched #1 · TMDB trending #2 · Stremio popular #4)`), so you can always see
*why* something is where it is.

### Turning the sources on (both free, two minutes each)

- **Trakt**: log in at trakt.tv, open https://trakt.tv/oauth/applications/new, give the app any name and the redirect
  URI `urn:ietf:wg:oauth:2.0:oob`, save. Copy the **Client ID** → repo → Settings → Secrets and variables → Actions →
  New repository secret `TRAKT_CLIENT_ID`.
- **TMDB**: themoviedb.org → Settings → API → request a (free) developer key. Copy the **API Key (v3 auth)** →
  New repository secret `TMDB_API_KEY`.

Then Actions → "Build & publish Top 10 addon" → *Run workflow*. The manifest description and the addon page say
which sources went into the current build.

Optional repository **variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Default | Meaning |
|---|---|---|
| `SOURCES` | all configured | force a subset, e.g. `trakt` for Trakt only, or `trakt,tmdb` |
| `TRAKT_PERIOD` | `weekly` | `daily`, `weekly` or `monthly` most-watched window |
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

Output lands in `dist/` (manifest.json, catalog/*.json, img/*.jpg). Without the keys it builds from Cinemeta alone.

## Files

- `generate.py` — fetches the lists, merges them, renders the numbered cards, writes `dist/`
- `fonts/ArchivoBlack-Regular.ttf` — numeral font (SIL Open Font License, from Google Fonts)
- `.github/workflows/build.yml` — scheduled build + Pages deployment
