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
from astropy.time import Time

from . import config
from . import uploads
from .cutouts import clean_and_reproject, make_grid, process_exposure
from .irsa import Exposure, UpstreamError, assign_epochs, query_exposures


@dataclass
class Job:
    key: str
    ra: float
    dec: float
    fov: float
    detectors: list[int]
    collections: list[str] = field(default_factory=list)
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
    fresh: bool = False          # search the archive even if a cached search exists
    dataset: str | None = None   # uploaded dataset id (None = SPHEREx archive)
    target: tuple | None = None  # user-chosen (ra, dec) for a dataset, else its centre
    pixel_scale: float = config.PIXEL_SCALE_ARCSEC
    units: str = "MJy/sr"
    info: dict = field(default_factory=dict)  # dataset summary: files used / rejected
    refresh_wait: float = 0.0    # seconds until this view may be refreshed again (last refresh request)

    def snapshot(self):
        with self.lock:
            return list(self.metas), list(self.images), list(self.variances)


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
_job_slots = threading.Semaphore(config.MAX_CONCURRENT_JOBS)


def job_key(ra: float, dec: float, fov: float, detectors: list[int], collections: list[str]) -> str:
    s = f"{ra:.4f},{dec:.4f},{fov:.3f},{sorted(detectors)},{sorted(collections)},{config.PIPELINE_VERSION}"
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def get_or_start(ra: float, dec: float, fov: float, detectors: list[int], collections: list[str],
                 refresh: bool = False) -> Job:
    """refresh=True re-runs the archive search for a finished view ('check for new data'), at most
    once per REFRESH_MIN_S per view so the archive cannot be hammered."""
    key = job_key(ra, dec, fov, detectors, collections)
    with _jobs_lock:
        job = _jobs.get(key) or _load_cached(key)
        if job and refresh and job.status in ("done", "error") and time.time() - job.started >= config.REFRESH_MIN_S:
            for p in _paths(key):
                p.unlink(missing_ok=True)
            job = Job(key, ra, dec, fov, sorted(detectors), list(collections), fresh=True)
        elif job and job.status != "error":
            job.refresh_wait = max(0.0, config.REFRESH_MIN_S - (time.time() - job.started)) if refresh else 0.0
            _jobs[key] = job
            return job
        else:
            job = Job(key, ra, dec, fov, sorted(detectors), list(collections))
        _jobs[key] = job
        if job.status != "done":
            threading.Thread(target=_run, args=(job,), daemon=True).start()
        return job


def get_or_start_dataset(dataset: str, ra: float | None, dec: float | None) -> Job:
    """Job for an uploaded dataset (optionally centred on a user-chosen target)."""
    if not uploads.DATASET_ID_RE.match(dataset):
        raise uploads.UploadError("invalid dataset id")
    target = (round(ra, 6), round(dec, 6)) if ra is not None and dec is not None else None
    key = hashlib.sha1(f"ds|{dataset}|{target}|{config.PIPELINE_VERSION}".encode()).hexdigest()[:16]
    with _jobs_lock:
        job = _jobs.get(key) or _load_cached(key)
        if job and job.status != "error":
            _jobs[key] = job
            return job
        job = Job(key, ra or 0.0, dec or 0.0, 0.0, [0], [f"upload:{dataset}"], dataset=dataset, target=target)
        _jobs[key] = job
        threading.Thread(target=_run, args=(job,), daemon=True).start()
        return job


def _run_dataset(job: Job):
    """Check every uploaded file against the standard, then align and stack like SPHEREx data."""
    job.status = "querying"
    names = [f["name"] for f in uploads.wait_for_manifest(job.dataset)["files"]]
    parsed, rejected = [], []

    def load(name):
        try:
            return uploads.read_upload(uploads.fetch(job.dataset, f"files/{name}"), name), None
        except uploads.UploadError as e:
            return None, {"name": name, "reason": str(e)}

    # Four at a time: a 100 MB upload can expand to several hundred MB in memory.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for ok, bad in pool.map(load, names):
            (parsed.append(ok) if ok else rejected.append(bad))
    if not parsed:
        raise uploads.UploadError("no file follows the upload standard: " +
                                  "; ".join(f"{r['name']}: {r['reason']}" for r in rejected[:5]))
    p = uploads.plan(parsed, *(job.target or (None, None)))
    job.ra, job.dec, job.fov, job.pixel_scale = p["ra"], p["dec"], p["fov"], p["pixel_scale_arcsec"]
    grid, n = make_grid(job.ra, job.dec, job.fov, job.pixel_scale)
    job.n, job.total = n, len(parsed)
    job.status = "fetching"
    parsed.sort(key=lambda f: f["mjd"])
    epochs = []
    for f in parsed:
        img, var = clean_and_reproject(f["image"], f["variance"], f["bad"], f["wcs"], grid, n)
        if np.isfinite(img).mean() < 0.05:
            rejected.append({"name": f["name"], "reason": "does not cover the target position"})
            continue
        i = len(epochs)
        t = Time(f["mjd"], format="mjd")
        epochs.append({"index": i, "mjd_start": f["mjd"], "mjd_end": f["mjd"], "mjd_mid": f["mjd"],
                       "date_mid": t.to_value("iso", subfmt="date_hm"), "decimal_year": float(t.decimalyear),
                       "n_exposures": 1})
        x, y = (float(v) for v in f["wcs"].world_to_pixel_values(job.ra, job.dec))
        with job.lock:
            job.metas.append({
                "obsid": f["name"], "detector": 0, "epoch": i, "mjd": f["mjd"], "date_obs": t.isot,
                "wavelength_um": f["wavelength_um"], "bandwidth_um": None, "psf_fwhm_arcsec": None,  # NaN is not valid JSON
                "version": "", "release": "upload", "source": "upload", "filter": f["filter"],
                "target_on_image": bool(0 <= x < f["shape"][1] and 0 <= y < f["shape"][0]),
                "target_flags": [], "contaminated": False, "saturated": False,
            })
            job.images.append(img)
            job.variances.append(var)
    job.epochs = epochs
    job.total = len(epochs)
    # Units from the files actually used (a skipped file must not hide a common BUNIT).
    units = {f["bunit"] for f in parsed if f["name"] in {m["obsid"] for m in job.metas}}
    job.units = units.pop() if len(units) == 1 and "" not in units else "data units"
    job.info = {"files_used": len(epochs), "rejected": rejected, "pixel_scale_arcsec": job.pixel_scale,
                "units": job.units}
    if not epochs:
        raise uploads.UploadError("no uploaded image covers the target position")


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
            if job.dataset:
                _run_dataset(job)
                job.status = "done"
                _save_cached(job)
                return
            job.status = "querying"
            exps = query_exposures(job.ra, job.dec, job.detectors, job.collections, fresh=job.fresh)
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
                        # Archive unreachable: don't hold a job slot for minutes of retries.
                        if job.failed >= config.ABORT_AFTER_FAILURES and not job.metas:
                            for other in futs:
                                other.cancel()
                            raise UpstreamError("IRSA image service is unreachable right now. Try again shortly.")
                        continue
                    with job.lock:
                        job.metas.append(r["meta"])
                        job.images.append(r["image"])
                        job.variances.append(r["variance"])
            job.status = "done"
            _save_cached(job)
        except (UpstreamError, uploads.UploadError) as e:
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


def _coadd(images: list[np.ndarray], variances: list[np.ndarray], idx: list[int]) -> np.ndarray:
    """Inverse-variance weighted mean of the selected frames (NaN where nothing is valid)."""
    im = np.stack([images[i] for i in idx])
    var = np.stack([variances[i] for i in idx])
    w = np.where(np.isfinite(im) & (var > 0), 1.0 / np.where(var > 0, var, 1), 0.0)
    wsum = w.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(wsum > 0, (np.nan_to_num(im) * w).sum(axis=0) / wsum, np.nan).astype(np.float32)


def epoch_frames(job: Job, wl_min=None, wl_max=None) -> list[dict]:
    """Inverse-variance weighted mean image per epoch."""
    metas, images, variances = job.snapshot()
    frames = []
    for ep in job.epochs:
        idx = [i for i, m in enumerate(metas) if m["epoch"] == ep["index"] and _in_range(m, wl_min, wl_max)]
        if not idx:
            frames.append({"epoch": ep, "n_used": 0, "image": None, "exposures": []})
            continue
        coadd = _coadd(images, variances, idx)
        wls = [metas[i]["wavelength_um"] for i in idx if metas[i]["wavelength_um"]]
        frames.append({
            "epoch": ep,
            "n_used": len(idx),
            "shallow": False,
            "image": coadd,
            "wavelength_min_um": min(wls) if wls else None,
            "wavelength_max_um": max(wls) if wls else None,
            "n_contaminated": sum(1 for i in idx if _contaminated(metas[i])),
            "n_saturated": sum(1 for i in idx if _saturated(metas[i])),
            "exposures": [metas[i]["obsid"] + f"D{metas[i]['detector']}" for i in idx],
        })
    # Epochs with few exposures are noisy and patchy; the UI skips them by default.
    # (Not for uploads: there each file is deliberately its own frame.)
    counts = [f["n_used"] for f in frames if f["n_used"]]
    if counts and not job.dataset:
        floor = max(config.MIN_EPOCH_EXPOSURES, 0.2 * float(np.median(counts)))
        for f in frames:
            f["shallow"] = f["n_used"] < floor
    return frames


def wavelength_frames(job: Job) -> list[dict]:
    """One stack per SPHEREx spectral channel, all epochs together, in wavelength order.

    Each detector's range is split into CHANNELS_PER_DETECTOR equal bins (17 x 6 = the 102 SPHEREx
    bands). Played as an animation this steps through the spectrum: sources with strong features,
    such as methane-absorbing brown dwarfs, fade and reappear. Moving sources smear, as all epochs
    are combined.
    """
    metas, images, variances = job.snapshot()
    frames = []
    if job.dataset:
        groups: dict = {}
        for i, m in enumerate(metas):
            groups.setdefault((m.get("wavelength_um") or 0.0, m.get("filter") or ""), []).append(i)
        for (wl, filt), idx in sorted(groups.items()):
            frames.append({"detector": 0, "channel": len(frames) + 1, "wavelength_um": wl or None, "filter": filt,
                           "wavelength_min_um": wl or None, "wavelength_max_um": wl or None, "n_used": len(idx),
                           "n_epochs": len({metas[i]["epoch"] for i in idx}), "n_contaminated": 0,
                           "image": _coadd(images, variances, idx)})
        return frames
    for det in job.detectors:
        lo, hi = config.DETECTOR_RANGES[det]
        edges = np.linspace(lo, hi, config.CHANNELS_PER_DETECTOR + 1)
        for k in range(config.CHANNELS_PER_DETECTOR):
            last = k == config.CHANNELS_PER_DETECTOR - 1
            idx = [i for i, m in enumerate(metas) if m["detector"] == det and m.get("wavelength_um") is not None
                   and edges[k] <= m["wavelength_um"] and (m["wavelength_um"] <= edges[k + 1] if last else m["wavelength_um"] < edges[k + 1])]
            frames.append({
                "detector": det, "channel": k + 1,
                "wavelength_um": float((edges[k] + edges[k + 1]) / 2),
                "wavelength_min_um": float(edges[k]), "wavelength_max_um": float(edges[k + 1]),
                "n_used": len(idx),
                "n_epochs": len({metas[i]["epoch"] for i in idx}),
                "n_contaminated": sum(1 for i in idx if _contaminated(metas[i])),
                "image": _coadd(images, variances, idx) if idx else None,
            })
    return frames


def exposure_list(job: Job) -> list[dict]:
    """Metadata for every processed exposure, oldest first (no pixels)."""
    metas, _, _ = job.snapshot()
    return sorted(({**m, "contaminated": _contaminated(m), "saturated": _saturated(m)} for m in metas),
                  key=lambda m: m["mjd"])


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
    to_mjy = 1.0 if job.dataset else omega_sr * 1e9  # SPHEREx: MJy/sr * sr -> mJy; uploads: own units

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
        "ra": job.ra, "dec": job.dec, "fov": job.fov, "detectors": job.detectors, "collections": job.collections, "n": job.n,
        "total": job.total, "failed": job.failed, "epochs": job.epochs, "metas": job.metas,
        "dataset": job.dataset, "pixel_scale": job.pixel_scale, "units": job.units, "info": job.info,
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
    job = Job(key, d["ra"], d["dec"], d["fov"], d["detectors"], d.get("collections", []), status="done", n=d["n"],
              total=d["total"], failed=d["failed"], epochs=d["epochs"], metas=d["metas"], started=d["saved"])
    job.dataset, job.units, job.info = d.get("dataset"), d.get("units", "MJy/sr"), d.get("info", {})
    job.pixel_scale = d.get("pixel_scale", config.PIXEL_SCALE_ARCSEC)
    job.images = list(arr["images"])
    job.variances = list(arr["variances"])
    return job
