# SPHEREx Sky Motion Explorer

A public web tool that blinks NASA SPHEREx infrared images of any patch of sky so anyone can
spot objects that move (nearby stars, brown dwarfs, asteroids) or change brightness (variable stars).
Built for the NASA "SPHEREx Sky Motion Explorer" challenge (see [docs/](docs/)).

## Why it works the way it does

SPHEREx is not a normal imager. Each detector sits behind a linear variable filter, so every
exposure sees each part of the sky at a *different wavelength*. Playing raw exposures in time
order would mostly show colour changes, not motion. The tool therefore:

1. Finds every SPHEREx spectral image that covers the position (IRSA TAP, live, so new weekly
   data appears automatically).
2. Groups exposures into **epochs**: SPHEREx revisits each point every ~6 months.
3. For each exposure: downloads a cutout, subtracts the ZODI model and residual sky, masks bad
   pixels from the FLAGS plane, and reprojects onto one north-up grid.
4. Stacks each epoch with inverse-variance weights per chosen detector (wavelength band).
5. The browser blinks the epochs with one shared stretch, so brightness changes are real.

Exposure mode plays single exposures in time order instead, for fast solar-system objects.

Object names resolve through CDS Sesame and are carried from J2000 to 2026.0 with their proper
motion. Barnard's Star has moved 4.5′ since 2000 and would otherwise be off-frame.

## Features

- Name or coordinate search (decimal, `hh:mm:ss ±dd:mm:ss`, `08h55m10.8s -07d14m42s`)
- Presets for the five challenge targets
- Epoch and exposure animation, blink speed, frame stepping (Space, ←, →)
- Detector (wavelength) selection D1–D6, field of view up to 15′
- Difference imaging (frame minus the mean of all frames)
- Gaia DR3 overlay with each star moved to the frame's date (fast movers in red)
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
npm run deploy          # builds the frontend, builds and pushes the container, deploys the Worker
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
