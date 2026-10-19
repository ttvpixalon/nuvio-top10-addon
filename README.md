# Top 10 Today — Nuvio / Stremio catalog addon

Two home-page rows, **Top 10 Movies Today** and **Top 10 Series Today**, showing what the world is watching right now
with Netflix-style ranked artwork (big outlined numerals next to the poster). The lists and the images are rebuilt
automatically every 6 hours by GitHub Actions and served from GitHub Pages — there is no server to run.

## Install in Nuvio

1. Open the addon page: `https://ttvpixalon.github.io/nuvio-top10-addon/` — it shows the manifest URL.
2. Nuvio → Settings → Addons → add `https://ttvpixalon.github.io/nuvio-top10-addon/manifest.json`.
3. Settings → Sources → Catalog order: drag the two Top 10 rows to the top of Home.

The artwork comes in two shapes and Nuvio picks the right one automatically: a 2:3 poster with the numeral in the
corner for poster rows, and a 16:9 tile with the numeral beside the poster for landscape rows.

## Where the "Top 10" comes from

| Source | When it is used | What it measures |
|---|---|---|
| **Cinemeta "Popular"** (default) | always works, no key needed | Stremio's global popularity feed |
| **TMDB "Trending today"** | when the `TMDB_API_KEY` repository secret is set | TMDB's worldwide daily trending |

Both are global and "right now". To switch to TMDB: repo → Settings → Secrets and variables → Actions → New
repository secret `TMDB_API_KEY` (free key from themoviedb.org/settings/api), then run the workflow once.

## Deploy your own copy

1. Create a new **public** repository on GitHub and push these files to its `main` branch.
2. Repo → Settings → Pages → Source: **GitHub Actions** (the workflow also tries to enable this itself).
3. Actions → "Build & publish Top 10 addon" → Run workflow. The site is live a minute later.

Optional repository variables: `TOP_N` (default 10), `SITE_URL` (only if you use a custom domain or a
`<user>.github.io` root repository). The schedule lives in `.github/workflows/build.yml`.

## Run locally

```
pip install pillow
SITE_URL=https://you.github.io/nuvio-top10-addon python generate.py
```

Output lands in `dist/` (manifest.json, catalog/*.json, img/*.jpg).

## Files

- `generate.py` — fetches the lists, resolves IMDb ids, renders the numbered cards, writes `dist/`
- `fonts/ArchivoBlack-Regular.ttf` — numeral font (SIL Open Font License, from Google Fonts)
- `.github/workflows/build.yml` — scheduled build + Pages deployment
