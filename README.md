# SPHEREx Sky Motion Explorer

A public web tool that blinks NASA SPHEREx infrared images of any patch of sky so anyone can
spot objects that move (nearby stars, brown dwarfs, asteroids) or change brightness (variable stars).
Built for the NASA "SPHEREx Sky Motion Explorer" challenge (see [docs/](docs/)).

**Live:** https://spherex.peykan.ai

## Why it works the way it does

SPHEREx is not a normal imager. Each detector sits behind a linear variable filter, so every
exposure sees each part of the sky at a *different wavelength*. Playing raw exposures in time
order would mostly show colour changes, not motion. The tool therefore:

1. Finds every SPHEREx spectral image that covers the position (IRSA SIA, with TAP as fallback;
   queried live, so new weekly data appears automatically). New data releases are picked up too:
   the backend lists the public S3 bucket's release folders once a day and searches the newest
   data release and quick release (set `SIA_COLLECTIONS` to pin them).
2. Groups exposures into **epochs**: SPHEREx revisits each point every ~6 months. Epochs with
   very few exposures are flagged "shallow" and skipped by default.
3. For each exposure: reads only the needed IMAGE and FLAGS rows plus the wavelength table
   straight from the public S3 bucket (HTTP range requests), subtracts the local sky, masks bad
   pixels, and reprojects onto one north-up grid.
4. Stacks each epoch, weighted by each frame's sky noise, per chosen detector (wavelength band).
5. The browser blinks the epochs with one shared stretch, so brightness changes are real.

Saturated stars (R Dra, RW Aur, Barnard's Star) have masked cores. Their brightness is measured
in a fixed ring around the core, and the brightness chart compares epochs at matched wavelengths
so different wavelength sampling is not mistaken for variability.

## Performance

A new position loads in about 10–30 s (first frames after ~5 s); results are then cached on
the container and in R2. Most of the speed comes from reading S3 directly: IRSA's cutout service
returns ~4 MB per exposure (mostly an unused PSF table) and takes ~3–5 s to build each one, while
the S3 range reads fetch under 1 MB in a few small requests. The IRSA cutout service remains as
a fallback.

## Deployment notes

- IRSA refuses connections from Cloudflare's container network, so the container reaches IRSA
  through the Worker's `/api/_irsa/` proxy. It only allows SIA, TAP and SPHEREx image paths and
  requires the `IRSA_PROXY_KEY` secret. S3, Gaia and CDS are reached directly.
- Containers roll over slowly. Bump `CONTAINER_GEN` in `worker/wrangler.jsonc` together with
  `BACKEND_GEN` in `backend/app/config.py` (a test checks they match) whenever the
  backend changes, so requests go to a fresh container running the new image.
- `GET /api/health` reports the pipeline version and whether the proxy is configured;
  `GET /api/diag` times each upstream from inside the container.

Exposure mode plays single exposures in time order instead, for fast solar-system objects.

Object names resolve through CDS Sesame and are carried from J2000 to 2026.0 with their proper
motion. Barnard's Star has moved 4.5′ since 2000 and would otherwise be off-frame.

## Features

- Name or coordinate search (decimal, `hh:mm:ss ±dd:mm:ss`, `08h55m10.8s -07d14m42s`)
- Presets for the five challenge targets
- Three animations: **epochs** (one stack per survey visit), **exposures** (time order, for fast
  solar-system objects) and **wavelength** (one stack per SPHEREx channel, 17 per detector, so the
  102 bands play as a spectrum); blink speed and frame stepping (Space, ←, →)
- Detector (wavelength) selection D1–D6, field of view up to 15′, viewer sized to the screen
- Difference imaging (frame minus the mean of all frames) and **A − B** (any frame minus another:
  two epochs to see what moved, or two wavelength channels for a colour image)
- Gaia DR3 overlay with each star moved to the frame's date (fast movers in red; brightest 80 shown)
- Reference panel beside the blink view: unWISE or Legacy Survey cutouts, a stack of all SPHEREx epochs,
  or **CDS Aladin Lite** (interactive; AllWISE, 2MASS and DSS imagery from IRSA's HiPS mirrors, Gaia DR3
  and CatWISE2020 overlays). `?ref=aladin` opens it directly. Aladin is loaded only when chosen.
- Per-frame metadata (date, wavelength range, exposures used, contamination flags) and an
  **exposure metadata table** for every exposure, with CSV export
- Click a source for a quick-look spectrum per epoch and a per-epoch brightness plot, plus CSV export
- **Data release choice** (sidebar, Data box): "Newest" (latest data release + quick release, the
  default), any single release found in the archive (`&release=dr1`), and optional Deep Field
  collections (`&deep=1`). Links that name a release keep showing the same data after new releases.
- **Check for new data**: searches the archive again for the current view (at most once an hour per
  view) and reports how many new exposures were found; cached results for that view are cleared.
- Shareable URLs: `?ra=133.788&dec=-7.242&fov=0.12&dets=5,6&mode=epoch`; add `&src=RA,Dec` to open a
  clicked source with its spectrum, and `&ref=aladin` (or `unwise`, `ls`, `spherex`) for the reference panel

## Your own images (upload standard "FITS + WCS v1")

Anyone can upload their own images ("Upload your own FITS images" under the search box); they are
checked, aligned, stacked and blinked exactly like SPHEREx data, with the same overlays and charts.

- One FITS file per exposure (`.fits`, `.fit`, `.fts`, optionally `.gz`).
- **Required:** a 2-D image (first image HDU, or an extension named `IMAGE`/`SCI`), a celestial WCS
  (RA/Dec), and an observation time (`MJD-OBS`, `MJD-AVG` or `DATE-OBS` in UTC).
- **Optional:** noise as `VARIANCE`, `ERR` (1σ) or `WEIGHT` (inverse variance); bad pixels as `MASK`,
  `FLAGS` or `DQ` (non-zero = bad); `WAVELEN` (µm) or `FILTER`; `BUNIT`.
- **Limits:** 50 files, 100 MB each (Cloudflare's request cap on this plan), 1 GB per dataset, images up
  to 6000 × 6000; per visitor, 3 new datasets and 60 files per minute.
- **Privacy:** each dataset gets an unguessable private link (`?dataset=<id>`); everything in the
  storage bucket is deleted automatically after 7 days (R2 lifecycle rule).
- Files that break the standard are skipped and listed with the reason; the target defaults to the
  median centre of the images, or can be given explicitly. Fluxes are reported in the files' own units.

## Embedding

Add `embed=1` to any view link to get just the blink viewer with a play button and a link back:

```html
<iframe src="https://spherex.peykan.ai/?q=WISE%20J085510.83-071442.5&ra=133.786245&dec=-7.244372&fov=0.120&dets=5,6&mode=epoch&embed=1"
        width="480" height="520" style="border:0" title="SPHEREx view of WISE 0855"></iframe>
```

## Layout

```
backend/   FastAPI + astropy/reproject science service (Docker image)
frontend/  React + Vite single-page app
worker/    Cloudflare Worker: serves the SPA, routes /api to the container, caches results in R2
docs/      Challenge brief and SPHEREx tutorial notebooks
```

## Run locally

```bash
# backend (Python 3.11)
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements.txt   # Windows path
cd backend && ../.venv/Scripts/python -m uvicorn app.main:app --port 8000 --reload

# frontend (Vite proxies /api to :8000)
cd frontend && npm install && npm run dev
```

Tests: `cd backend && ../.venv/Scripts/python -m pytest tests`

## Deploy (Cloudflare)

Requires the Workers Paid plan (Containers) and Docker running locally for the image build.

```bash
cd worker
npm install
npx wrangler login
npx wrangler r2 bucket create spherex-cache
npx wrangler secret put IRSA_PROXY_KEY   # any long random string
npm run deploy          # builds the frontend, builds and pushes the container, deploys the Worker
python ../backend/prewarm.py https://spherex.peykan.ai   # fill the cache for the five targets
```

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/resolve?q=` | Name or coordinates to position at the data epoch |
| `GET /api/frames?ra&dec&fov&dets&mode&release&deep&refresh` | Starts or polls a job; `mode` = epoch, exposure or wavelength; `release` = auto or e.g. dr1; `refresh=1` searches again; frames as base64 float32 |
| `POST /api/datasets`, `PUT /api/datasets/<id>/files/<name>` | Create a dataset, upload one FITS file (raw body) |
| `GET /api/releases` | Releases found in the archive, and what "Newest" currently means |
| `GET /api/exposures?ra&dec&fov&dets` | Metadata for every exposure (no pixels) |
| `GET /api/photometry?ra&dec&fov&dets&x&y` | Aperture photometry at a grid pixel for every exposure |
| `GET /api/gaia?ra&dec&radius` | Gaia DR3 sources (epoch 2016.0) |

## Data credits

NASA SPHEREx via NASA/IPAC Infrared Science Archive (IRSA); ESA Gaia DR3; CDS Sesame/SIMBAD.
