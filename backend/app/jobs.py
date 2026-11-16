"""Background jobs: query -> parallel cutout processing -> per-epoch coadds, with disk caching.

Results are usable while a job is still running: epochs fill in round-robin order so every
frame of the animation appears early and sharpens as more exposures arrive.
"""
from __future__ import annotations

import hashlib
import json
import threading
from types import SimpleNamespace
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
    movers: dict | None = None   # moving-source search result, computed once the job is done
    contams: list = field(default_factory=list)  # per-exposure contamination masks (uint8), aligned with images
    refresh_wait: float = 0.0    # seconds until this view may be refreshed again (last refresh request)
    planned: list = field(default_factory=list)  # every exposure the archive search returned (obsid, detector, mjd)
    polled: float = field(default_factory=time.time)  # last time someone asked for this job (queue priority)
    parts: list = field(default_factory=list)    # a view's detector jobs: [{detector, status, done, total}]

    def snapshot(self):
        with self.lock:
            return list(self.metas), list(self.images), list(self.variances)


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
ABANDON_S = 45  # a queued job nobody has asked about for this long gives up its place


class _Slots:
    """At most MAX_CONCURRENT_JOBS jobs process at once. A free slot goes to the waiting job that
    was asked for most recently (the view someone is looking at), not the oldest one; a queued job
    nobody asks about any more gives up its place, and starts again if it is asked for later."""

    def __init__(self, n: int):
        self.cv = threading.Condition()
        self.free = n
        self.waiting: list[Job] = []

    def acquire(self, job: Job) -> bool:
        with self.cv:
            self.waiting.append(job)
            try:
                while True:
                    if time.time() - job.polled > ABANDON_S:
                        return False
                    if self.free > 0 and job is max(self.waiting, key=lambda j: j.polled):
                        self.free -= 1
                        return True
                    self.cv.wait(timeout=2)
            finally:
                self.waiting.remove(job)
                self.cv.notify_all()

    def release(self):
        with self.cv:
            self.free += 1
            self.cv.notify_all()


_slots = _Slots(config.MAX_CONCURRENT_JOBS)


def job_key(ra: float, dec: float, fov: float, detectors: list[int], collections: list[str]) -> str:
    s = f"{ra:.4f},{dec:.4f},{fov:.3f},{sorted(detectors)},{sorted(collections)},{config.PIPELINE_VERSION}"
    return hashlib.sha1(s.encode()).hexdigest()[:16]


_restore_locks: dict[str, threading.Lock] = {}


def _restore(key: str) -> Job | None:
    """A finished job from the disk cache. Called without the global lock: loading a large job
    takes a moment."""
    with _restore_locks.setdefault(key, threading.Lock()):
        return _load_cached(key)


ALL_DETECTORS = [1, 2, 3, 4, 5, 6]
_views: dict[str, tuple[tuple, Job]] = {}


def get_or_start(ra: float, dec: float, fov: float, detectors: list[int], collections: list[str],
                 refresh: bool = False) -> Job:
    """A view: one job per detector, combined. Each detector is processed (and stored) once, so
    adding a detector processes only that one, and removing one or returning to a set seen before
    needs no download at all."""
    dets = sorted(set(detectors))
    parts = [_get_or_start_part(ra, dec, fov, [d], collections, refresh) for d in dets]
    key = job_key(ra, dec, fov, dets, collections)
    sig = tuple((p.key, p.status, len(p.metas), p.started) for p in parts)
    with _jobs_lock:
        cached = _views.get(key)
        if cached and cached[0] == sig:
            view = cached[1]  # unchanged parts: same object, so its movers result is kept
        else:
            view = _combine(key, ra, dec, fov, dets, collections, parts)
            _views[key] = (sig, view)
    view.refresh_wait = max((p.refresh_wait for p in parts), default=0.0)
    return view


def _epoch_of(mjd: float, epochs: list[dict]) -> int:
    for e in epochs:
        if e["mjd_start"] - 1 <= mjd <= e["mjd_end"] + 1:
            return e["index"]
    return min(epochs, key=lambda e: abs(e["mjd_mid"] - mjd))["index"] if epochs else -1


def _combine(key, ra, dec, fov, dets, collections, parts: list[Job]) -> Job:
    """The view's job, assembled from its detector jobs (no pixels are copied)."""
    view = Job(key, ra, dec, fov, dets, list(collections))
    statuses = {p.status for p in parts}
    errors = [p.error for p in parts if p.status == "error"]
    if errors:
        view.status, view.error = "error", errors[0]
    elif statuses == {"done"}:
        view.status = "done"
    elif "fetching" in statuses or any(p.metas for p in parts):
        view.status = "fetching"
    elif "querying" in statuses:
        view.status = "querying"
    else:
        view.status = "queued"
    view.n = max((p.n for p in parts), default=0)
    view.total = sum(p.total for p in parts)
    view.failed = sum(p.failed for p in parts)
    view.started = min((p.started for p in parts), default=time.time())
    view.fresh = any(p.fresh for p in parts)
    metas, planned = [], []
    for p in parts:
        with p.lock:
            m, im, v, c = list(p.metas), list(p.images), list(p.variances), list(p.contams)
        metas += m
        view.images += im
        view.variances += v
        view.contams += c + [None] * (len(im) - len(c))
        planned += p.planned or [{"obsid": x.get("obsid"), "detector": x.get("detector"), "mjd": x["mjd"]} for x in m]
    # Epochs over every exposure the view will have, as a single job for these detectors would
    # group them; each measured exposure then joins its epoch by (obsid, detector).
    items = [SimpleNamespace(mjd=q["mjd"], epoch=-1, k=(q.get("obsid"), q.get("detector"))) for q in planned]
    view.epochs = assign_epochs(items)
    ep_of = {it.k: it.epoch for it in items}
    view.metas = [{**m, "epoch": ep_of.get((m.get("obsid"), m.get("detector")), _epoch_of(m["mjd"], view.epochs))}
                  for m in metas]
    view.planned = planned
    view.parts = [{"detector": p.detectors[0], "status": p.status, "done": len(p.metas), "total": p.total}
                  for p in parts]
    return view


def _get_or_start_part(ra: float, dec: float, fov: float, detectors: list[int], collections: list[str],
                       refresh: bool = False) -> Job:
    """refresh=True re-runs the archive search for a finished view ('check for new data'), at most
    once per REFRESH_MIN_S per view so the archive cannot be hammered."""
    key = job_key(ra, dec, fov, detectors, collections)
    with _jobs_lock:
        job = _jobs.get(key)
    if job is None:
        job = _restore(key)
    with _jobs_lock:
        job = _jobs.get(key) or job
        if job and refresh and job.status in ("done", "error") and time.time() - job.started >= config.REFRESH_MIN_S:
            for p in _paths(key):
                p.unlink(missing_ok=True)
            job = Job(key, ra, dec, fov, sorted(detectors), list(collections), fresh=True)
        elif job and job.status != "error":
            job.refresh_wait = max(0.0, config.REFRESH_MIN_S - (time.time() - job.started)) if refresh else 0.0
            job.polled = time.time()
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
        job = _jobs.get(key)
    if job is None:
        job = _restore(key)
    with _jobs_lock:
        job = _jobs.get(key) or job
        if job and job.status != "error":
            job.polled = time.time()
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
            job.contams.append(None)  # uploads carry no contamination flags
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
    if not _slots.acquire(job):
        # Abandoned while queued: forget it, so a later request starts it afresh.
        with _jobs_lock:
            if _jobs.get(job.key) is job:
                del _jobs[job.key]
        return
    try:
        try:
            if job.dataset:
                _run_dataset(job)
                job.status = "done"
                _save_cached(job)
                return
            job.status = "querying"
            # One search for all detectors (the archive returns them all anyway): the other
            # detector jobs of this position reuse it.
            exps = query_exposures(job.ra, job.dec, ALL_DETECTORS, job.collections, fresh=job.fresh)
            exps = [e for e in exps if e.detector in job.detectors]
            exps = _limit(exps, config.MAX_EXPOSURES_PER_DETECTOR * len(job.detectors))
            job.planned = [{"obsid": e.obsid, "detector": e.detector, "mjd": e.mjd} for e in exps]
            job.epochs = assign_epochs(exps)
            if not exps:
                job.status = "done"
                _save_cached(job)
                return
            grid, n = make_grid(job.ra, job.dec, job.fov)
            job.n, job.total = n, len(exps)
            job.status = "fetching"
            with ThreadPoolExecutor(max_workers=config.PART_FETCH_WORKERS) as pool:
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
                        job.contams.append(r.get("contam"))
            job.status = "done"
            _save_cached(job)
        except (UpstreamError, uploads.UploadError) as e:
            job.status, job.error = "error", str(e)
        except Exception as e:  # keep the server alive; report to client
            traceback.print_exc()
            job.status, job.error = "error", f"processing failed: {e}"
    finally:
        _slots.release()


# ---------------------------------------------------------------- products

def _contaminated(m: dict) -> bool:
    """Computed from the stored flag names so cached results follow config changes."""
    return bool(set(m.get("target_flags", [])) & config.CONTAMINATION_FLAG_NAMES)


def _contaminated_at(m: dict, mask: np.ndarray | None, ap: np.ndarray) -> bool:
    """Contaminated where it is measured: any flagged pixel in the aperture. The exposure's flags
    at the field centre only stand in when there is no mask (uploaded data has none)."""
    if mask is None or mask.shape != ap.shape:
        return _contaminated(m)
    return bool(mask[ap].any())


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


def _contam_map(job: Job, idx: list[int]) -> np.ndarray | None:
    """Fraction of the frame's exposures flagged as contaminated at each pixel, scaled 0-255."""
    with job.lock:
        masks = [job.contams[i] for i in idx if i < len(job.contams) and job.contams[i] is not None]
    if not masks:
        return None
    return np.round(np.mean(np.stack(masks), axis=0) * 255).astype(np.uint8)


def _flag_counts(metas: list[dict], idx: list[int]) -> dict:
    """{flag name: number of exposures with that contamination flag at the target}."""
    counts: dict[str, int] = {}
    for i in idx:
        for f in metas[i].get("target_flags", []):
            if f in config.CONTAMINATION_FLAG_NAMES:
                counts[f] = counts.get(f, 0) + 1
    return counts


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
            "flag_counts": _flag_counts(metas, idx),
            "contam": _contam_map(job, idx),
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
                           "image": _coadd(images, variances, idx), "contam": None, "flag_counts": {}})
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
                "flag_counts": _flag_counts(metas, idx), "contam": _contam_map(job, idx) if idx else None,
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
    if len(order) > config.MAX_EXPOSURES:  # all six detectors can hold far more than a browser should get
        order = [order[k] for k in sorted(set(np.linspace(0, len(order) - 1, config.MAX_EXPOSURES).round().astype(int)))]
    return [{"meta": {**metas[i], "contaminated": _contaminated(metas[i]), "saturated": _saturated(metas[i])},
             "image": images[i], "contam": _contam_map(job, [i])} for i in order]


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
    with job.lock:
        contams = list(job.contams)
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
    for i, (m, im, var) in enumerate(zip(metas, images, variances)):
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
                    "contaminated": _contaminated_at(m, contams[i] if i < len(contams) else None, ap),
                    "saturated": _saturated(m)})
    return sorted(out, key=lambda p: p["mjd"]), mode


# ---------------------------------------------------------------- disk cache

def _paths(key: str):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CACHE_DIR / f"{key}.json", config.CACHE_DIR / f"{key}.npz"


def _save_cached(job: Job):
    jp, npzp = _paths(job.key)
    np.savez_compressed(npzp,
                        images=np.stack(job.images) if job.images else np.zeros((0, 0, 0), np.float32),
                        variances=np.stack(job.variances) if job.variances else np.zeros((0, 0, 0), np.float32),
                        contams=np.stack([c if c is not None else np.zeros_like(job.images[0], np.uint8) for c in job.contams])
                        if job.contams and job.images else np.zeros((0, 0, 0), np.uint8))
    jp.write_text(json.dumps({
        "ra": job.ra, "dec": job.dec, "fov": job.fov, "detectors": job.detectors, "collections": job.collections, "n": job.n,
        "total": job.total, "failed": job.failed, "epochs": job.epochs, "metas": job.metas,
        "dataset": job.dataset, "pixel_scale": job.pixel_scale, "units": job.units, "info": job.info,
        "planned": job.planned, "saved": time.time(),
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
    job.planned = d.get("planned", [])
    job.pixel_scale = d.get("pixel_scale", config.PIXEL_SCALE_ARCSEC)
    job.images = list(arr["images"])
    job.contams = list(arr["contams"]) if "contams" in arr.files else [None] * len(job.images)
    job.variances = list(arr["variances"])
    return job
