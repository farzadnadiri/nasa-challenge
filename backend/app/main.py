"""SPHEREx Sky Motion Explorer API."""
from __future__ import annotations

import base64

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config, gaia, jobs
from .cutouts import make_grid
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


def _fov(fov: float) -> float:
    return min(max(fov, config.MIN_FOV_DEG), config.MAX_FOV_DEG)


def _job(ra: float, dec: float, fov: float, dets: str) -> jobs.Job:
    tier = next(t for t in config.JOB_FOV_TIERS_DEG if t >= _fov(fov) - 1e-9)
    return jobs.get_or_start(ra % 360.0, dec, tier, _detectors(dets))


def _crop(job: jobs.Job, fov: float) -> tuple[int, int]:
    """(n, offset) of the requested field inside the job grid; both sizes are odd, so it stays centred."""
    if not job.n:
        return 0, 0
    n = min(make_grid(job.ra, job.dec, _fov(fov))[1], job.n)
    return n, (job.n - n) // 2


def _cut(img, n: int, off: int):
    return None if img is None else img[off:off + n, off:off + n]


def _cached_response(body: dict, complete: bool) -> JSONResponse:
    # The Cloudflare Worker stores responses in R2 only when X-Complete is true.
    headers = {"X-Complete": "true" if complete else "false",
               "Cache-Control": "public, max-age=86400" if complete else "no-store"}
    return JSONResponse(body, headers=headers)


@app.get("/api/health")
def health():
    return {"ok": True, "pipeline_version": config.PIPELINE_VERSION,
            "irsa_base": config.IRSA_BASE, "irsa_proxy_key_set": bool(config.IRSA_PROXY_KEY)}


@app.get("/api/diag")
def diag():
    """Time a small request to each upstream from wherever this server runs (ops check)."""
    import socket
    import time

    import httpx

    checks = {
        "irsa_sia": (config.IRSA_SIA + "?COLLECTION=spherex_dr1&POS=circle+269.4463+4.7682+0.0003&RESPONSEFORMAT=CSV", 60),
        "irsa_tap": (config.IRSA_TAP + "?LANG=ADQL&FORMAT=csv&QUERY=SELECT+TOP+1+ra+FROM+fp_psc", 30),
        "irsa_home": (config.IRSA_BASE, 20),
        "gaia": (config.GAIA_TAP.replace("/sync", "/availability"), 20),
        "sesame": (config.SESAME + "?R%20Dra", 20),
        "irsa_http80": ("http://irsa.ipac.caltech.edu/", 20),
        "s3_bucket": ("https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/?list-type=2&max-keys=1&prefix=dr1/level2/", 20),
    }
    out = {"dns": {}}
    for host in ("irsa.ipac.caltech.edu", "gea.esac.esa.int"):
        try:
            out["dns"][host] = sorted({a[4][0] for a in socket.getaddrinfo(host, 443)})
        except OSError as e:
            out["dns"][host] = f"error: {e}"
    for name, (url, timeout) in checks.items():
        t = time.time()
        try:
            r = httpx.get(url, timeout=timeout, follow_redirects=True)
            out[name] = {"status": r.status_code, "seconds": round(time.time() - t, 2), "bytes": len(r.content)}
        except Exception as e:  # report the failure type, that is the point of this endpoint
            out[name] = {"error": f"{type(e).__name__}: {e}"[:200], "seconds": round(time.time() - t, 2)}
    return out


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
    n, off = _crop(job, fov)
    complete = job.status == "done"
    body = {
        "key": job.key,
        "status": job.status,
        "error": job.error,
        "progress": {"done": len(job.metas), "total": job.total, "failed": job.failed},
        "grid": {"n": n, "ra": job.ra, "dec": job.dec, "fov": _fov(fov),
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
                body["frames"].append({**f, "image": _b64(_cut(img, n, off))})
        else:
            for f in jobs.exposure_frames(job, wl_min, wl_max):
                body["frames"].append({"meta": f["meta"], "image": _b64(_cut(f["image"], n, off))})
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
    _, off = _crop(job, fov)  # x, y are in the (cropped) grid the client sees
    pts, phot_mode = jobs.photometry(job, x + off, y + off, r_ap=r, r_in=r * 2, r_out=r * 3.5)
    return _cached_response({"status": job.status, "mode": phot_mode, "points": pts}, job.status == "done")


@app.get("/api/gaia")
def gaia_cone(ra: float = Query(..., ge=0, le=360), dec: float = Query(..., ge=-90, le=90),
              radius: float = Query(0.08, gt=0, le=0.5)):
    try:
        return _cached_response(gaia.cone(ra, dec, radius), True)
    except UpstreamError as e:
        raise HTTPException(503, str(e))
