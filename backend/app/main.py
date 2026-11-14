"""SPHEREx Sky Motion Explorer API."""
from __future__ import annotations

import base64

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config, gaia, jobs
from .irsa import UpstreamError
from .resolve import resolve as resolve_query

app = FastAPI(title="SPHEREx Sky Motion Explorer API", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


def _b64(a: np.ndarray | None) -> str | None:
    if a is None:
        return None
    return base64.b64encode(np.ascontiguousarray(a, dtype="<f4").tobytes()).decode()


def _detectors(s: str) -> list[int]:
    try:
        d = sorted({int(x) for x in s.split(",") if x.strip()})
    except ValueError:
        raise HTTPException(400, "dets must be a comma-separated list of 1-6")
    if not d or any(x < 1 or x > 6 for x in d):
        raise HTTPException(400, "dets must be a comma-separated list of 1-6")
    return d


def _job(ra: float, dec: float, fov: float, dets: str) -> jobs.Job:
    fov = min(max(fov, config.MIN_FOV_DEG), config.MAX_FOV_DEG)
    return jobs.get_or_start(ra % 360.0, dec, fov, _detectors(dets))


def _cached_response(body: dict, complete: bool) -> JSONResponse:
    # The Cloudflare Worker stores responses in R2 only when X-Complete is true.
    headers = {"X-Complete": "true" if complete else "false",
               "Cache-Control": "public, max-age=86400" if complete else "no-store"}
    return JSONResponse(body, headers=headers)


@app.get("/api/health")
def health():
    return {"ok": True, "pipeline_version": config.PIPELINE_VERSION}


@app.get("/api/resolve")
def resolve(q: str = Query(..., min_length=1, max_length=200)):
    try:
        return resolve_query(q)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except UpstreamError as e:
        raise HTTPException(503, str(e))


@app.get("/api/frames")
def frames(
    ra: float = Query(..., ge=0, le=360),
    dec: float = Query(..., ge=-90, le=90),
    fov: float = Query(0.1, description="field of view, degrees"),
    dets: str = Query("5,6", description="SPHEREx detectors 1-6, comma separated"),
    mode: str = Query("epoch", pattern="^(epoch|exposure)$"),
    wl_min: float | None = Query(None, description="micron"),
    wl_max: float | None = Query(None, description="micron"),
):
    job = _job(ra, dec, fov, dets)
    complete = job.status == "done"
    body = {
        "key": job.key,
        "status": job.status,
        "error": job.error,
        "progress": {"done": len(job.metas), "total": job.total, "failed": job.failed},
        "grid": {"n": job.n, "ra": job.ra, "dec": job.dec, "fov": job.fov,
                 "pixel_scale_arcsec": config.PIXEL_SCALE_ARCSEC},
        "detectors": job.detectors,
        "epochs": job.epochs,
        "mode": mode,
        "frames": [],
    }
    if job.status in ("fetching", "done") and job.metas:
        if mode == "epoch":
            for f in jobs.epoch_frames(job, wl_min, wl_max):
                img = f.pop("image")
                body["frames"].append({**f, "image": _b64(img)})
        else:
            for f in jobs.exposure_frames(job, wl_min, wl_max):
                body["frames"].append({"meta": f["meta"], "image": _b64(f["image"])})
    return _cached_response(body, complete)


@app.get("/api/photometry")
def photometry(
    ra: float = Query(..., ge=0, le=360),
    dec: float = Query(..., ge=-90, le=90),
    fov: float = Query(0.1),
    dets: str = Query("5,6"),
    x: float = Query(..., description="grid pixel x (0-based)"),
    y: float = Query(..., description="grid pixel y (0-based)"),
    r: float = Query(2.0, gt=0, le=10),
):
    job = _job(ra, dec, fov, dets)
    if job.status not in ("fetching", "done"):
        return _cached_response({"status": job.status, "error": job.error, "points": []}, False)
    pts, phot_mode = jobs.photometry(job, x, y, r_ap=r, r_in=r * 2, r_out=r * 3.5)
    return _cached_response({"status": job.status, "mode": phot_mode, "points": pts}, job.status == "done")


@app.get("/api/gaia")
def gaia_cone(ra: float = Query(..., ge=0, le=360), dec: float = Query(..., ge=-90, le=90),
              radius: float = Query(0.08, gt=0, le=0.5)):
    try:
        return _cached_response(gaia.cone(ra, dec, radius), True)
    except UpstreamError as e:
        raise HTTPException(503, str(e))
