"""Moving-source detection: our discovery engine.

A source that moves between SPHEREx epochs but has no Gaia counterpart moving with it is a
discovery candidate (very red objects such as brown dwarfs are often missing from Gaia, which
sees optical light). Classic astronomy pipeline, no image-processing heuristics:

  1. detect sources in every deep epoch stack (sep), with windowed sub-pixel centroids and a
     positional error from the signal-to-noise;
  2. link detections across epochs (mutual nearest neighbour inside a motion-limited radius,
     consistent brightness), keeping tracks seen in at least MIN_EPOCHS epochs;
  3. fit straight-line motion x(t), y(t) weighted by the positional errors -> velocity, its
     uncertainty and a goodness of fit;
  4. compare with Gaia DR3 carried to each epoch's date: a matching Gaia star moving the same way
     makes it a known mover; no match makes it a candidate.
"""
from __future__ import annotations

import math

import numpy as np
import sep
from scipy import ndimage

from . import config, gaia
from .cutouts import make_grid
from .irsa import UpstreamError

MIN_EPOCHS = 3            # a track must be seen in this many epochs (rejects artifacts)
DETECT_SIGMA = 4.0        # detection threshold
MAX_PM_ARCSEC_YR = 15.0   # fastest motion searched for (Barnard's Star: 10.4"/yr)
LINK_TOL_PX = 1.5         # extra search radius on top of the motion allowance
FLUX_RATIO_MAX = 4.0      # linked detections must agree in brightness within this factor
POS_FLOOR_PX = 0.12       # systematic position error (resampling, wavelength sampling)
MIN_SIGNIFICANCE = 5.0    # motion must be this many sigma
MIN_PM_ARCSEC_YR = 0.3    # and at least this fast
MAX_CHI2_RED = 10.0       # and consistent with a straight line
GAIA_MATCH_PX = 1.5       # Gaia star within this distance (median over epochs) = same object


def _coadd_with_variance(images, variances, idx):
    im = np.stack([images[i] for i in idx]).astype(np.float64)
    var = np.stack([variances[i] for i in idx]).astype(np.float64)
    w = np.where(np.isfinite(im) & (var > 0), 1.0 / np.where(var > 0, var, 1), 0.0)
    wsum = w.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        img = np.where(wsum > 0, (np.nan_to_num(im) * w).sum(axis=0) / wsum, np.nan)
        v = np.where(wsum > 0, 1.0 / wsum, np.nan)
    return img, v


def detect(img: np.ndarray, var: np.ndarray, fwhm_hint_px: float) -> dict:
    """Sources in one epoch stack: x, y (grid px), position error, flux, S/N, saturated flag."""
    bad = ~np.isfinite(img)
    if bad.all():
        return {k: np.array([]) for k in ("x", "y", "err", "flux", "snr", "sat")}  # nothing to measure
    # Masked pixels (saturated cores, gaps) take the nearest valid value, so a saturated star is
    # still one blob with a sensible centroid instead of a ring.
    idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
    data = img[tuple(idx)].astype(np.float64)
    good_var = var[np.isfinite(var) & (var > 0)]
    sky_var = float(np.median(good_var)) if good_var.size else float(np.nanvar(data))
    err = np.sqrt(np.where(np.isfinite(var) & (var > 0), var, sky_var))
    n = data.shape[0]
    bkg = sep.Background(np.ascontiguousarray(data), bw=max(16, n // 4), bh=max(16, n // 4))
    data = np.ascontiguousarray(data - bkg.back())
    err = np.ascontiguousarray(err)
    try:
        objs = sep.extract(data, DETECT_SIGMA, err=err, minarea=3, deblend_cont=0.005)
    except Exception:  # sep raises when a field is extremely crowded; nothing usable then
        objs = np.zeros(0, dtype=[("x", float), ("y", float), ("flux", float), ("a", float), ("b", float), ("flag", int)])
    if len(objs) == 0:
        return {k: np.array([]) for k in ("x", "y", "err", "flux", "snr", "sat")}
    sig = max(fwhm_hint_px, 1.0) / 2.355
    xw, yw, _ = sep.winpos(data, objs["x"], objs["y"], np.full(len(objs), sig))
    flux, ferr, _ = sep.sum_circle(data, xw, yw, np.full(len(objs), 2.0 * sig * 2.355 / 2), err=err)
    snr = np.where(ferr > 0, flux / ferr, 0.0)
    fwhm = 2.355 * np.sqrt(np.clip(objs["a"] * objs["b"], 1e-6, None))
    edge = (xw < 3) | (yw < 3) | (xw > n - 4) | (yw > n - 4)
    keep = (snr > DETECT_SIGMA) & ~edge & np.isfinite(xw) & np.isfinite(yw)
    xi = np.clip(np.round(xw).astype(int), 0, n - 1)
    yi = np.clip(np.round(yw).astype(int), 0, n - 1)
    sat = bad[yi, xi]
    pos_err = np.hypot(np.maximum(fwhm, fwhm_hint_px) / (2.355 * np.maximum(snr, 1e-3)), POS_FLOOR_PX)
    return {"x": xw[keep], "y": yw[keep], "err": pos_err[keep], "flux": flux[keep], "snr": snr[keep], "sat": sat[keep],
            "_data": data, "_err": err, "_sig": sig, "_fwhm": fwhm_hint_px}


def measure_at(d: dict, x: float, y: float) -> dict | None:
    """Forced measurement at a predicted position: recovers a source that fell just below the
    detection threshold in one epoch (e.g. a shallow epoch). Needs 3 sigma within 1.5 px."""
    if "_data" not in d:
        return None
    n = d["_data"].shape[0]
    if not (3 <= x <= n - 4 and 3 <= y <= n - 4):
        return None
    xw, yw, _ = sep.winpos(d["_data"], np.array([x]), np.array([y]), np.array([d["_sig"]]))
    if not (np.isfinite(xw[0]) and np.isfinite(yw[0])) or math.hypot(xw[0] - x, yw[0] - y) > 1.5:
        return None
    flux, ferr, _ = sep.sum_circle(d["_data"], xw, yw, np.array([d["_sig"] * 2.355]), err=d["_err"])
    snr = float(flux[0] / ferr[0]) if ferr[0] > 0 else 0.0
    if snr < 3.0:
        return None
    err = math.hypot(d["_fwhm"] / (2.355 * snr), POS_FLOOR_PX)
    return {"x": float(xw[0]), "y": float(yw[0]), "err": err, "flux": float(flux[0]), "snr": snr, "sat": False}


def _fit(t, x, y, e):
    """Weighted straight-line fit of x(t), y(t). Returns velocity (px/yr) with errors and chi2."""
    w = 1.0 / e ** 2
    tm = float((w * t).sum() / w.sum())
    dt = t - tm
    s_tt = float((w * dt * dt).sum())
    if s_tt <= 0:
        return None
    out = {"t_mid": tm}
    chi2 = 0.0
    for name, v in (("x", x), ("y", y)):
        mean = float((w * v).sum() / w.sum())
        slope = float((w * dt * v).sum() / s_tt)
        out[name] = mean
        out["v" + name] = slope
        out["sv" + name] = math.sqrt(1.0 / s_tt)
        chi2 += float((w * (v - mean - slope * dt) ** 2).sum())
    out["chi2_red"] = chi2 / max(1, 2 * len(t) - 4)
    return out


def link(dets: list[dict], times: list[float], scale_arcsec: float) -> list[dict]:
    """Tracks across epochs, anchored on the epoch with the most detections."""
    if len(dets) < MIN_EPOCHS:
        return []
    ref = int(np.argmax([len(d["x"]) for d in dets]))
    vmax = MAX_PM_ARCSEC_YR / scale_arcsec
    tracks = []
    for i in range(len(dets[ref]["x"])):
        rx, ry, rf = dets[ref]["x"][i], dets[ref]["y"][i], dets[ref]["flux"][i]
        members = [(ref, i)]
        for e, d in enumerate(dets):
            if e == ref or len(d["x"]) == 0:
                continue
            radius = vmax * abs(times[e] - times[ref]) + LINK_TOL_PX
            dist = np.hypot(d["x"] - rx, d["y"] - ry)
            j = int(np.argmin(dist))
            if dist[j] > radius:
                continue
            if not (1 / FLUX_RATIO_MAX < (d["flux"][j] / rf if rf > 0 else 0) < FLUX_RATIO_MAX):
                continue
            # mutual nearest: that detection's closest reference source must be this one
            back = np.hypot(dets[ref]["x"] - d["x"][j], dets[ref]["y"] - d["y"][j])
            if int(np.argmin(back)) != i:
                continue
            members.append((e, j))
        if len(members) >= MIN_EPOCHS:
            tracks.append(members)
    return tracks


def find_movers(job, frames: list[dict], debug: bool = False) -> dict:
    """Moving sources in a finished job. frames: epoch frames (dicts with 'epoch' and 'shallow')."""
    metas, images, variances = job.snapshot()
    epochs = [f["epoch"] for f in frames if f.get("n_used") and not f.get("shallow")]
    if len(epochs) < MIN_EPOCHS:
        return {"movers": [], "n_epochs": len(epochs), "note": f"needs at least {MIN_EPOCHS} deep epochs"}
    scale = job.pixel_scale
    psf = [m.get("psf_fwhm_arcsec") for m in metas if m.get("psf_fwhm_arcsec")]
    fwhm_px = max(1.2, (float(np.median(psf)) if psf else 1.5 * scale) / scale * 1.3)  # reprojection widens it
    dets, times = [], []
    for ep in epochs:
        idx = [i for i, m in enumerate(metas) if m["epoch"] == ep["index"]]
        img, var = _coadd_with_variance(images, variances, idx)
        dets.append(detect(img, var, fwhm_px))
        times.append(float(ep["decimal_year"]))
    times_a = np.array(times)
    grid, n = make_grid(job.ra, job.dec, job.fov, scale)

    gaia_rows = []
    try:
        gaia_rows = gaia.cone(job.ra, job.dec, job.fov * 0.75)["sources"]
    except UpstreamError:
        pass
    g_ra = np.array([g["ra"] for g in gaia_rows]) if gaia_rows else np.zeros(0)
    g_dec = np.array([g["dec"] for g in gaia_rows]) if gaia_rows else np.zeros(0)
    g_pmra = np.array([g.get("pmra") or 0.0 for g in gaia_rows]) if gaia_rows else np.zeros(0)
    g_pmdec = np.array([g.get("pmdec") or 0.0 for g in gaia_rows]) if gaia_rows else np.zeros(0)

    def gaia_px_at(year):
        dt = year - gaia.GAIA_EPOCH
        dec = g_dec + g_pmdec * dt / 3.6e6
        ra = g_ra + g_pmra * dt / 3.6e6 / np.cos(np.radians(g_dec))
        return grid.wcs_world2pix(ra, dec, 0) if len(ra) else (np.zeros(0), np.zeros(0))

    gaia_cache = {e: gaia_px_at(times[e]) for e in range(len(times))}
    # Pass 1: points for every linked track, plus forced measurements in epochs where the source
    # was not detected (predicted from the fit), then a straight-line fit.
    fitted, n_tracks = [], 0
    for members in link(dets, times, scale):
        n_tracks += 1
        pts = [{"e": e, **{k: dets[e][k][j] for k in ("x", "y", "err", "flux", "snr", "sat")}} for e, j in members]
        fit = _fit(times_a[[p["e"] for p in pts]], np.array([p["x"] for p in pts]),
                   np.array([p["y"] for p in pts]), np.array([p["err"] for p in pts]))
        if not fit:
            continue
        have = {p["e"] for p in pts}
        for e in range(len(dets)):
            if e in have:
                continue
            dt = times[e] - fit["t_mid"]
            got = measure_at(dets[e], fit["x"] + fit["vx"] * dt, fit["y"] + fit["vy"] * dt)
            if got:
                pts.append({"e": e, **got, "forced": True})
        pts.sort(key=lambda p: p["e"])
        if len(pts) > len(members):
            fit = _fit(times_a[[p["e"] for p in pts]], np.array([p["x"] for p in pts]),
                       np.array([p["y"] for p in pts]), np.array([p["err"] for p in pts]))
            if not fit:
                continue
        fitted.append((pts, fit))

    # Calibrate the position errors on this field: almost every source is still, so the median
    # goodness of fit says how far the error model is off (clamped to a factor of 0.3 to 3).
    chis = [f["chi2_red"] for _, f in fitted]
    k_err = float(np.clip(math.sqrt(np.median(chis)), 0.3, 3.0)) if len(chis) >= 8 else 1.0

    # Pass 2: decide which tracks are real movers, then compare with Gaia.
    movers, tracks_dbg = [], []
    for pts, fit in fitted:
        e_ix = np.array([p["e"] for p in pts])
        v = math.hypot(fit["vx"], fit["vy"])
        sv = k_err * math.sqrt((fit["vx"] * fit["svx"]) ** 2 + (fit["vy"] * fit["svy"]) ** 2) / v if v > 0 else 1e9
        signif = v / sv if sv > 0 else 0.0
        chi2 = fit["chi2_red"] / k_err ** 2
        pm_arcsec = v * scale
        baseline = float(times_a[e_ix].max() - times_a[e_ix].min())
        snr_med = float(np.median([p["snr"] for p in pts]))
        if debug:
            cx, cy = (float(v_) for v_ in grid.wcs_pix2world([[fit["x"], fit["y"]]], 0)[0])
            tracks_dbg.append({"ra": cx, "dec": cy, "pm_arcsec_yr": pm_arcsec, "significance": signif,
                               "chi2_red": chi2, "n": len(pts), "forced": sum(1 for p in pts if p.get("forced")),
                               "baseline_yr": baseline, "snr": snr_med, "err_px": float(np.median([p["err"] for p in pts]))})
        if not (signif >= MIN_SIGNIFICANCE and pm_arcsec >= MIN_PM_ARCSEC_YR and chi2 <= MAX_CHI2_RED
                and v * baseline >= 0.5):
            continue
        # Gaia: is a catalogued star sitting on this track at (most) epochs?
        match = None
        if len(g_ra):
            dists, idxs = [], []
            for p in pts:
                gx, gy = gaia_cache[p["e"]]
                d = np.hypot(gx - p["x"], gy - p["y"])
                j = int(np.argmin(d))
                dists.append(d[j])
                idxs.append(j)
            j = max(set(idxs), key=idxs.count)
            if float(np.median(dists)) <= GAIA_MATCH_PX:
                g = gaia_rows[j]
                g_pm = math.hypot(g.get("pmra") or 0.0, g.get("pmdec") or 0.0) / 1000.0
                # a nearby Gaia star that does not move like the track is not the same object
                if g_pm >= 0.5 * pm_arcsec:
                    match = {"source_id": g["source_id"], "name": g.get("name"), "pm_arcsec_yr": g_pm,
                             "g_mag": g.get("phot_g_mean_mag")}
        ra_mid, dec_mid = (float(v_) for v_ in grid.wcs_pix2world([[fit["x"], fit["y"]]], 0)[0])
        pos = []
        for p in pts:
            r_, d_ = (float(v_) for v_ in grid.wcs_pix2world([[p["x"], p["y"]]], 0)[0])
            pos.append({"epoch": int(epochs[p["e"]]["index"]), "year": times[p["e"]], "ra": r_, "dec": d_,
                        "forced": bool(p.get("forced"))})
        movers.append({
            "status": "known" if match else "candidate",
            "ra": ra_mid, "dec": dec_mid, "year_mid": fit["t_mid"],
            # grid x grows to the west (east is left), y to the north
            "pmra_mas_yr": -fit["vx"] * scale * 1000.0, "pmdec_mas_yr": fit["vy"] * scale * 1000.0,
            "pm_arcsec_yr": pm_arcsec, "significance": signif, "chi2_red": chi2,
            "n_epochs": len(pts), "snr": snr_med, "saturated": bool(any(p["sat"] for p in pts)),
            "gaia": match, "positions": pos,
        })
    movers.sort(key=lambda m: (m["status"] != "candidate", -m["significance"]))
    extra = {"tracks": tracks_dbg, "detections_per_epoch": [len(d["x"]) for d in dets], "years": times,
             "error_scale": k_err} if debug else {}
    return {**extra, "movers": movers, "n_epochs": len(epochs), "n_tracks": n_tracks,
            "n_sources": int(np.median([len(d["x"]) for d in dets])), "gaia_checked": bool(len(g_ra))}
