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
   queried live, so new weekly data appears automatically).
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
- Containers roll over slowly. Bump `CONTAINER_GEN` in `worker/wrangler.jsonc` whenever the
  backend changes, so requests go to a fresh container running the new image.
- `GET /api/health` reports the pipeline version and whether the proxy is configured;
  `GET /api/diag` times each upstream from inside the container.

Exposure mode plays single exposures in time order instead, for fast solar-system objects.

Object names resolve through CDS Sesame and are carried from J2000 to 2026.0 with their proper
motion. Barnard's Star has moved 4.5′ since 2000 and would otherwise be off-frame.

## Features

- Name or coordinate search (decimal, `hh:mm:ss ±dd:mm:ss`, `08h55m10.8s -07d14m42s`)
- Presets for the five challenge targets
- Epoch and exposure animation, blink speed, frame stepping (Space, ←, →)
- Detector (wavelength) selection D1–D6, field of view up to 15′, viewer sized to the screen
- Difference imaging (frame minus the mean of all frames)
- Gaia DR3 overlay with each star moved to the frame's date (fast movers in red; brightest 80 shown)
- Per-frame metadata: date, wavelength range, exposures used, contamination flags
- Click a source for a quick-look spectrum per epoch and a per-epoch brightness plot, plus CSV export
- Shareable URLs: `?ra=133.788&dec=-7.242&fov=0.12&dets=5,6&mode=epoch`

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
| `GET /api/frames?ra&dec&fov&dets&mode&wl_min&wl_max` | Starts or polls a job; returns epoch or exposure frames (base64 float32) |
| `GET /api/photometry?ra&dec&fov&dets&x&y` | Aperture photometry at a grid pixel for every exposure |
| `GET /api/gaia?ra&dec&radius` | Gaia DR3 sources (epoch 2016.0) |

## Data credits

NASA SPHEREx via NASA/IPAC Infrared Science Archive (IRSA); ESA Gaia DR3; CDS Sesame/SIMBAD.
