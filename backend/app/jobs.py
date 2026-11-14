"""Background jobs: query -> parallel cutout processing -> per-epoch coadds, with disk caching.

Results are usable while a job is still running: epochs fill in round-robin order so every
frame of the animation appears early and sharpens as more exposures arrive.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import numpy as np

from . import config
from .cutouts import make_grid, process_exposure
from .irsa import Exposure, UpstreamError, assign_epochs, query_exposures


@dataclass
class Job:
    key: str
    ra: float
    dec: float
    fov: float
    detectors: list[int]
    status: str = "queued"          # queued | querying | fetching | done | error
    error: str | None = None
    n: int = 0
    total: int = 0
    failed: int = 0
    epochs: list[dict] = field(default_factory=list)
    metas: list[dict] = field(default_factory=list)
    images: list[np.ndarray] = field(default_factory=list)
    variances: list[np.ndarray] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self):
        with self.lock:
            return list(self.metas), list(self.images), list(self.variances)


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
_job_slots = threading.Semaphore(config.MAX_CONCURRENT_JOBS)


def job_key(ra: float, dec: float, fov: float, detectors: list[int]) -> str:
    s = f"{ra:.4f},{dec:.4f},{fov:.3f},{sorted(detectors)},{config.PIPELINE_VERSION}"
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def get_or_start(ra: float, dec: float, fov: float, detectors: list[int]) -> Job:
    key = job_key(ra, dec, fov, detectors)
    with _jobs_lock:
        job = _jobs.get(key)
        if job and job.status != "error":
            return job
        job = _load_cached(key) or Job(key, ra, dec, fov, sorted(detectors))
        _jobs[key] = job
        if job.status != "done":
            threading.Thread(target=_run, args=(job,), daemon=True).start()
        return job


def _interleave(exps: list[Exposure]) -> list[Exposure]:
    """Round-robin across epochs so every epoch gets data early."""
    by_epoch: dict[int, list[Exposure]] = {}
    for e in exps:
        by_epoch.setdefault(e.epoch, []).append(e)
    queues = [v for _, v in sorted(by_epoch.items())]
    out = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


def _limit(exps: list[Exposure], limit: int) -> list[Exposure]:
    if len(exps) <= limit:
        return exps
    idx = np.linspace(0, len(exps) - 1, limit).round().astype(int)
    return [exps[i] for i in sorted(set(idx))]


def _run(job: Job):
    with _job_slots:
        try:
            job.status = "querying"
            exps = query_exposures(job.ra, job.dec, job.detectors)
            exps = _limit(exps, config.MAX_EXPOSURES)
            job.epochs = assign_epochs(exps)
            if not exps:
                job.status = "done"
                _save_cached(job)
                return
            grid, n = make_grid(job.ra, job.dec, job.fov)
            job.n, job.total = n, len(exps)
            job.status = "fetching"
            with ThreadPoolExecutor(max_workers=config.FETCH_WORKERS) as pool:
                futs = {pool.submit(process_exposure, e, job.ra, job.dec, job.fov, grid, n): e
                        for e in _interleave(exps)}
                for f in as_completed(futs):
                    try:
                        r = f.result()
                    except Exception:
                        job.failed += 1
                        continue
                    with job.lock:
                        job.metas.append(r["meta"])
                        job.images.append(r["image"])
                        job.variances.append(r["variance"])
            job.status = "done"
            _save_cached(job)
        except UpstreamError as e:
            job.status, job.error = "error", str(e)
        except Exception as e:  # keep the server alive; report to client
            traceback.print_exc()
            job.status, job.error = "error", f"processing failed: {e}"


# ---------------------------------------------------------------- products

def _contaminated(m: dict) -> bool:
    """Computed from the stored flag names so cached results follow config changes."""
    return bool(set(m.get("target_flags", [])) & config.CONTAMINATION_FLAG_NAMES)


def _saturated(m: dict) -> bool:
    return bool(set(m.get("target_flags", [])) & config.SATURATION_FLAG_NAMES)


def _in_range(m: dict, wl_min: float | None, wl_max: float | None) -> bool:
    wl = m.get("wavelength_um")
    if wl is None:
        return wl_min is None and wl_max is None
    return (wl_min is None or wl >= wl_min) and (wl_max is None or wl <= wl_max)


def epoch_frames(job: Job, wl_min=None, wl_max=None) -> list[dict]:
    """Inverse-variance weighted mean image per epoch."""
    metas, images, variances = job.snapshot()
    frames = []
    for ep in job.epochs:
        idx = [i for i, m in enumerate(metas) if m["epoch"] == ep["index"] and _in_range(m, wl_min, wl_max)]
        if not idx:
            frames.append({"epoch": ep, "n_used": 0, "image": None, "exposures": []})
            continue
        im = np.stack([images[i] for i in idx])
        var = np.stack([variances[i] for i in idx])
        w = np.where(np.isfinite(im) & (var > 0), 1.0 / np.where(var > 0, var, 1), 0.0)
        wsum = w.sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            coadd = np.where(wsum > 0, (np.nan_to_num(im) * w).sum(axis=0) / wsum, np.nan)
        wls = [metas[i]["wavelength_um"] for i in idx if metas[i]["wavelength_um"]]
        frames.append({
            "epoch": ep,
            "n_used": len(idx),
            "shallow": False,
            "image": coadd.astype(np.float32),
            "wavelength_min_um": min(wls) if wls else None,
            "wavelength_max_um": max(wls) if wls else None,
            "n_contaminated": sum(1 for i in idx if _contaminated(metas[i])),
            "n_saturated": sum(1 for i in idx if _saturated(metas[i])),
            "exposures": [metas[i]["obsid"] + f"D{metas[i]['detector']}" for i in idx],
        })
    # Epochs with few exposures are noisy and patchy; the UI skips them by default.
    counts = [f["n_used"] for f in frames if f["n_used"]]
    if counts:
        floor = max(config.MIN_EPOCH_EXPOSURES, 0.2 * float(np.median(counts)))
        for f in frames:
            f["shallow"] = f["n_used"] < floor
    return frames


def exposure_frames(job: Job, wl_min=None, wl_max=None) -> list[dict]:
    metas, images, _ = job.snapshot()
    order = sorted((i for i, m in enumerate(metas) if _in_range(m, wl_min, wl_max)), key=lambda i: metas[i]["mjd"])
    return [{"meta": {**metas[i], "contaminated": _contaminated(metas[i]), "saturated": _saturated(metas[i])},
             "image": images[i]} for i in order]


def photometry(job: Job, x: float, y: float, r_ap: float = 2.0, r_in: float = 4.0, r_out: float = 7.0) -> list[dict]:
    """Aperture photometry at grid pixel (x, y) on every exposure (quick-look only).

    Flux in mJy using the nominal pixel solid angle; not a substitute for IRSA's
    spectrophotometry tool.

    Saturated stars have a masked core. Then the flux of one fixed ring just outside the
    largest masked core is measured instead ("wing" flux): the same ring on every exposure,
    so it tracks relative brightness but is not a total flux.
    Returns (points, mode) with mode "aperture" or "wing".
    """
    metas, images, variances = job.snapshot()
    if not images:
        return [], "aperture"
    n = images[0].shape[0]
    yy, xx = np.mgrid[0:n, 0:n]
    d = np.hypot(xx - x, yy - y)
    ap = d <= r_ap
    omega_sr = (config.PIXEL_SCALE_ARCSEC / 206264.806) ** 2
    to_mjy = omega_sr * 1e9  # MJy/sr * sr -> MJy -> mJy

    near = d <= r_out
    core = [d[near & ~np.isfinite(im)] for im in images]
    core_r = [float(c.max()) for c in core if c.size and c.min() <= r_ap]
    mode = "aperture"
    if len(core_r) > 0.3 * len(images):  # saturated in a good share of exposures
        mode = "wing"
        r1 = max(core_r) + 0.75
        ap = (d > r1) & (d <= r1 + 2.0)
        r_in, r_out = r1 + 4.0, r1 + 7.0
    ann = (d >= r_in) & (d <= r_out)

    out = []
    for m, im, var in zip(metas, images, variances):
        a = im[ap]
        ok = np.isfinite(a)
        # Aperture: every pixel needed. Wing ring: tolerate a few masked pixels, scaled up.
        if not ok.all() and (mode == "aperture" or ok.mean() < 0.7):
            continue
        sky = np.nanmedian(im[ann]) if np.isfinite(im[ann]).sum() > 5 else 0.0
        flux = (a[ok] - sky).sum() * (a.size / ok.sum()) * to_mjy
        err = np.sqrt(np.nansum(var[ap])) * to_mjy
        out.append({"mjd": m["mjd"], "epoch": m["epoch"], "detector": m["detector"],
                    "wavelength_um": m["wavelength_um"], "flux_mjy": float(flux), "err_mjy": float(err),
                    "contaminated": _contaminated(m), "saturated": _saturated(m)})
    return sorted(out, key=lambda p: p["mjd"]), mode


# ---------------------------------------------------------------- disk cache

def _paths(key: str):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CACHE_DIR / f"{key}.json", config.CACHE_DIR / f"{key}.npz"


def _save_cached(job: Job):
    jp, npzp = _paths(job.key)
    np.savez_compressed(npzp,
                        images=np.stack(job.images) if job.images else np.zeros((0, 0, 0), np.float32),
                        variances=np.stack(job.variances) if job.variances else np.zeros((0, 0, 0), np.float32))
    jp.write_text(json.dumps({
        "ra": job.ra, "dec": job.dec, "fov": job.fov, "detectors": job.detectors, "n": job.n,
        "total": job.total, "failed": job.failed, "epochs": job.epochs, "metas": job.metas,
        "saved": time.time(),
    }))


def _load_cached(key: str) -> Job | None:
    jp, npzp = _paths(key)
    if not (jp.exists() and npzp.exists()):
        return None
    d = json.loads(jp.read_text())
    if time.time() - d["saved"] > config.CACHE_TTL_DAYS * 86400:
        return None
    arr = np.load(npzp)
    job = Job(key, d["ra"], d["dec"], d["fov"], d["detectors"], status="done", n=d["n"],
              total=d["total"], failed=d["failed"], epochs=d["epochs"], metas=d["metas"])
    job.images = list(arr["images"])
    job.variances = list(arr["variances"])
    return job
