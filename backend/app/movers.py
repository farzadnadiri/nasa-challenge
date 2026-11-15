"""Moving-source detection: our discovery engine.

A source that moves between SPHEREx epochs but has no Gaia counterpart moving with it is a
discovery candidate (very red objects such as brown dwarfs are often missing from Gaia, which
sees optical light). Classic astronomy pipeline, no image-processing heuristics:

  1. detect sources in every deep epoch stack (sep), with windowed sub-pixel centroids and a
     positional error from the signal-to-noise; only where the epoch has real coverage;
  2. link detections across epochs: any epoch can start a track, straight-line motions through
     every nearby detection are tried, and each detection ends up in at most one track;
  3. fill epochs a track missed with forced measurements at the predicted position;
  4. calibrate the position errors on the field itself (almost every source is still), with a
     separate systematic floor and signal-to-noise term so bright and faint sources are both right;
  5. fit straight-line motion -> velocity, its uncertainty and a goodness of fit;
  6. compare with Gaia DR3 carried to each epoch's date. A Gaia star moving the same way makes a
     known mover; a Gaia star that stays on the track but moves differently means the measured
     motion is doubtful (as is a saturated track); no Gaia star at all makes a candidate.
"""
from __future__ import annotations

import math

import numpy as np
import sep
from scipy import ndimage
from scipy.spatial import cKDTree
from scipy.stats import chi2 as chi2_dist

from . import gaia
from .cutouts import make_grid
from .irsa import UpstreamError

MIN_EPOCHS = 3            # a track must be seen in this many epochs (rejects artifacts)
DETECT_SIGMA = 4.0        # detection threshold
MAX_PM_ARCSEC_YR = 15.0   # fastest motion searched for (Barnard's Star: 10.4"/yr)
LINK_TOL_PX = 1.5         # a linked detection must lie this close to the straight-line prediction
SAME_DET_PX = 1.0         # a forced position this close to a detection is that detection
FLUX_RATIO_MAX = 4.0      # linked detections must agree in brightness within this factor
POS_FLOOR_PX = 0.12       # default systematic position error, before calibration on the field
MIN_FLOOR_PX = 0.05       # never below: parallax (up to ~0.1 px for the nearest stars) is not modelled
SAT_FLOOR_PX = 0.12       # points with saturated pixels in the aperture: the centroid is less certain
MIN_SIGNIFICANCE = 5.0    # motion must be this many sigma
MIN_PM_ARCSEC_YR = 0.3    # and at least this fast
MAX_CHI2_RED = 10.0       # and consistent with a straight line
OUTLIER_SIGMA = 4.0       # one point this far off the line (after calibration) is dropped
FORCED_SNR = 3.0          # forced measurement: signal-to-noise needed
FORCED_SHIFT_PX = 1.5     # and largest centroid shift from the prediction
SAT_HOLE_MAX_PX = 200     # masked regions inside the field up to this size are saturated cores
GAIA_MATCH_PX = 1.5       # Gaia star within this distance (median over epochs) = same object
GAIA_MAX_ROWS = 20000     # deep enough to reach the faint stars our stacks detect
GAIA_MARGIN_DEG = 0.02    # stars that move into the field over the survey (7"/yr for 10 yr)
SEEN_FRACTION = 0.6       # a mover must be seen in this share of the epochs that cover its track
JACKKNIFE_SIGMA = 3.0     # the motion must survive dropping any one point at this significance
CALIB_MIN_TRACKS = 6      # tracks needed to calibrate the errors at all
CALIB_TWO_TERM = 16       # and to fit the floor and the S/N term separately

_FIELDS = ("x", "y", "stat", "err", "flux", "snr", "sat")


def _coadd_with_variance(images, variances, idx):
    im = np.stack([images[i] for i in idx]).astype(np.float64)
    var = np.stack([variances[i] for i in idx]).astype(np.float64)
    w = np.where(np.isfinite(im) & (var > 0), 1.0 / np.where(var > 0, var, 1), 0.0)
    wsum = w.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        img = np.where(wsum > 0, (np.nan_to_num(im) * w).sum(axis=0) / wsum, np.nan)
        v = np.where(wsum > 0, 1.0 / wsum, np.nan)
    return img, v


def _epoch_time(ep: dict, metas: list[dict], variances: list[np.ndarray]) -> float:
    """Effective date of an epoch stack: the exposure dates weighted like the stack (an epoch
    can span 30 days, which matters for the fastest stars)."""
    year = float(ep["decimal_year"])
    if "mjd_mid" not in ep or not metas or any("mjd" not in m for m in metas):
        return year
    w = []
    for v in variances:
        good = v[np.isfinite(v) & (v > 0)]
        w.append(1.0 / float(np.median(good)) if good.size else 0.0)
    w = np.array(w)
    if w.sum() <= 0:
        return year
    mjd = float((w * np.array([m["mjd"] for m in metas])).sum() / w.sum())
    return year + (mjd - float(ep["mjd_mid"])) / 365.25


def _empty(extra: dict | None = None) -> dict:
    return {**{k: np.array([]) for k in _FIELDS}, **(extra or {})}


def detect(img: np.ndarray, var: np.ndarray, fwhm_hint_px: float) -> dict:
    """Sources in one epoch stack: x, y (grid px), position error, flux, S/N, saturated flag."""
    bad = ~np.isfinite(img)
    if bad.all():
        return _empty()  # nothing to measure
    n = img.shape[0]
    # Masked pixels are of two kinds. Small holes inside the field (saturated cores, bad pixels)
    # take the nearest valid value, so a saturated star is still one blob with a sensible centroid.
    # The area this epoch did not cover must never look like data: filling it would smear the
    # edge pixels outwards and give a star cut by the edge a false centroid (false motion).
    off = np.zeros_like(bad)
    lab, nlab = ndimage.label(bad)
    if nlab:
        sizes = np.asarray(ndimage.sum(bad, lab, index=np.arange(1, nlab + 1)))
        outside = np.zeros(nlab + 1, bool)
        outside[1:] = sizes > SAT_HOLE_MAX_PX
        outside[np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))] = True
        outside[0] = False
        off = outside[lab]
    holes = bad & ~off
    idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
    data = img[tuple(idx)].astype(np.float64)
    good_var = var[np.isfinite(var) & (var > 0) & ~bad]
    sky_var = float(np.median(good_var)) if good_var.size else float(np.nanvar(img))
    err = np.ascontiguousarray(np.sqrt(np.where(np.isfinite(var) & (var > 0), var, sky_var)), dtype=np.float64)
    bw = max(16, n // 4)
    mask = np.ascontiguousarray(off)
    bkg = sep.Background(np.ascontiguousarray(data), mask=mask, bw=bw, bh=bw)
    data = data - bkg.back()
    data[off] = 0.0
    data = np.ascontiguousarray(data)
    dist_off = ndimage.distance_transform_edt(~off) if off.any() else np.full(bad.shape, np.inf)
    dist_hole = ndimage.distance_transform_edt(~holes) if holes.any() else np.full(bad.shape, np.inf)
    sig = max(fwhm_hint_px, 1.0) / 2.355
    r_ap = sig * 2.355  # aperture radius: one FWHM
    base = {"_data": data, "_err": err, "_sig": sig, "_r": r_ap, "_fwhm": fwhm_hint_px,
            "_off": dist_off, "_hole": dist_hole}
    try:
        # clean=False: SExtractor-style cleaning folds faint sources near bright stars into the bright
        # star, which loses exactly the faint movers we look for (still wing detections cannot fake motion)
        objs = sep.extract(data, DETECT_SIGMA, err=err, mask=mask, minarea=3, deblend_cont=0.005, clean=False)
    except Exception:  # sep raises when a field is extremely crowded; nothing usable then
        return _empty(base)
    if len(objs) == 0:
        return _empty(base)
    xw, yw, _ = sep.winpos(data, objs["x"], objs["y"], np.full(len(objs), sig))
    inside = np.isfinite(xw) & np.isfinite(yw) & (xw >= 3) & (yw >= 3) & (xw <= n - 4) & (yw <= n - 4)
    objs, xw, yw = objs[inside], xw[inside], yw[inside]
    if len(xw) == 0:
        return _empty(base)
    flux, ferr, _ = sep.sum_circle(data, xw, yw, np.full(len(xw), r_ap), err=err)
    snr = np.where(ferr > 0, flux / ferr, 0.0)
    fwhm = 2.355 * np.sqrt(np.clip(objs["a"] * objs["b"], 1e-6, None))
    xi, yi = np.round(xw).astype(int), np.round(yw).astype(int)
    covered = dist_off[yi, xi] > r_ap + 1.0   # the whole aperture lies on real data of this epoch
    sat = dist_hole[yi, xi] <= r_ap           # masked (saturated) pixels inside the aperture
    keep = (snr > DETECT_SIGMA) & covered
    stat = np.maximum(fwhm, fwhm_hint_px) / (2.355 * np.maximum(snr, 1e-3))
    return {**base, "x": xw[keep], "y": yw[keep], "stat": stat[keep], "err": np.hypot(stat[keep], POS_FLOOR_PX),
            "flux": flux[keep], "snr": snr[keep], "sat": sat[keep]}


def measure_at(d: dict, x: float, y: float) -> dict | None:
    """Forced measurement at a predicted position: recovers a source that fell just below the
    detection threshold in one epoch. Needs FORCED_SNR within FORCED_SHIFT_PX, on covered sky."""
    if "_data" not in d:
        return None
    n = d["_data"].shape[0]
    if not (3 <= x <= n - 4 and 3 <= y <= n - 4):
        return None
    if d["_off"][int(round(y)), int(round(x))] <= d["_r"] + 1.0:
        return None
    xw, yw, _ = sep.winpos(d["_data"], np.array([x]), np.array([y]), np.array([d["_sig"]]))
    if not (np.isfinite(xw[0]) and np.isfinite(yw[0])) or math.hypot(xw[0] - x, yw[0] - y) > FORCED_SHIFT_PX:
        return None
    flux, ferr, _ = sep.sum_circle(d["_data"], xw, yw, np.array([d["_r"]]), err=d["_err"])
    snr = float(flux[0] / ferr[0]) if ferr[0] > 0 else 0.0
    if snr < FORCED_SNR:
        return None
    stat = d["_fwhm"] / (2.355 * snr)
    xi, yi = int(round(xw[0])), int(round(yw[0]))
    return {"x": float(xw[0]), "y": float(yw[0]), "stat": stat, "err": math.hypot(stat, POS_FLOOR_PX),
            "flux": float(flux[0]), "snr": snr, "sat": bool(d["_hole"][yi, xi] <= d["_r"])}


def _covered(d: dict, x: float, y: float) -> bool:
    """Does this epoch have real data (not an edge or a gap) around this position?"""
    if "_data" not in d:
        return False
    n = d["_data"].shape[0]
    if not (3 <= x <= n - 4 and 3 <= y <= n - 4):
        return False
    return bool(d["_off"][int(round(y)), int(round(x))] > d["_r"] + 1.0)


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
    res = np.zeros_like(t, dtype=float)
    for name, v in (("x", x), ("y", y)):
        mean = float((w * v).sum() / w.sum())
        slope = float((w * dt * v).sum() / s_tt)
        out[name] = mean
        out["v" + name] = slope
        out["sv" + name] = math.sqrt(1.0 / s_tt)
        r = v - mean - slope * dt
        res += r * r
        chi2 += float((w * r * r).sum())
    out["dof"] = 2 * len(t) - 4
    out["chi2"] = chi2
    out["chi2_red"] = chi2 / max(1, out["dof"])
    out["norm_res"] = np.sqrt(res) / e   # each point's distance from the line, in its own errors
    return out


def _fit_pts(pts: list[dict], times: np.ndarray, key: str = "err"):
    return _fit(times[[p["e"] for p in pts]], np.array([p["x"] for p in pts]),
                np.array([p["y"] for p in pts]), np.array([p[key] for p in pts]))


def _jackknife_min(pts: list[dict], times: np.ndarray) -> float:
    """Smallest motion significance when any one point is left out: a motion that rests on a
    single point (one bad centroid) collapses here."""
    worst = math.inf
    for i in range(len(pts)):
        f = _fit_pts(pts[:i] + pts[i + 1:], times, "cal")
        if not f or f["svx"] <= 0:
            return 0.0
        worst = min(worst, math.hypot(f["vx"], f["vy"]) / f["svx"])
    return worst


def _flux_ok(f: float, ref: float) -> bool:
    return ref > 0 and 1 / FLUX_RATIO_MAX < f / ref < FLUX_RATIO_MAX


def link(dets: list[dict], times: list[float], scale_arcsec: float) -> list[dict]:
    """Tracks across epochs, each {epoch: detection index}.

    Every detection of the starting epochs proposes a track: straight-line motions through each
    brightness-consistent detection within reach in another epoch (and no motion at all) are tried,
    and the one collecting the most detections, then the closest ones, wins. A track with
    MIN_EPOCHS detections has one in any E - MIN_EPOCHS + 1 epochs, so starting from that many
    epochs finds every track, even one missing from the richest epoch. Proposals are then accepted
    best first, plausible fits before implausible ones, and a detection goes to one track only.
    """
    E = len(dets)
    if E < MIN_EPOCHS:
        return []
    vmax = MAX_PM_ARCSEC_YR / scale_arcsec
    pos = [np.column_stack([d["x"], d["y"]]) if len(d["x"]) else np.zeros((0, 2)) for d in dets]
    trees = [cKDTree(p) if len(p) else None for p in pos]
    starts = [int(a) for a in np.argsort([-len(p) for p in pos], kind="stable")[: E - MIN_EPOCHS + 1]]
    proposals: dict[tuple, dict] = {}
    for a in starts:
        if not len(pos[a]):
            continue
        near = {e: trees[e].query_ball_point(pos[a], vmax * abs(times[e] - times[a]) + LINK_TOL_PX)
                for e in range(E) if e != a and trees[e] is not None and abs(times[e] - times[a]) > 1e-3}
        for i in range(len(pos[a])):
            fa = dets[a]["flux"][i]
            ce, cj = [], []
            for e, lists in near.items():
                for j in lists[i]:
                    if _flux_ok(dets[e]["flux"][j], fa):
                        ce.append(e)
                        cj.append(j)
            if len(set(ce)) < MIN_EPOCHS - 1:
                continue
            ce_a, cj_a = np.array(ce), np.array(cj)
            cp = np.array([pos[e][j] for e, j in zip(ce, cj)])
            ct = np.array([times[e] for e in ce]) - times[a]
            # hypotheses: no motion, and the motion through each candidate
            V = np.vstack([np.zeros((1, 2)), (cp - pos[a][i]) / ct[:, None]])
            res = np.linalg.norm(cp[None, :, :] - (pos[a][i] + V[:, None, :] * ct[None, :, None]), axis=2)
            res = np.where(res <= LINK_TOL_PX, res, np.inf)
            eu = np.unique(ce_a)
            cols = [np.flatnonzero(ce_a == e) for e in eu]
            best = np.stack([res[:, c].min(axis=1) for c in cols], axis=1)        # (H, epochs)
            pick = np.stack([c[res[:, c].argmin(axis=1)] for c in cols], axis=1)  # (H, epochs)
            found = np.isfinite(best)
            count = found.sum(axis=1)
            ssq = np.where(found, best, 0.0) ** 2
            h = int(np.lexsort((ssq.sum(axis=1), -count))[0])
            if count[h] + 1 < MIN_EPOCHS:
                continue
            members = {a: i}
            for k, e in enumerate(eu):
                if found[h, k]:
                    members[int(e)] = int(cj_a[pick[h, k]])
            proposals[tuple(sorted(members.items()))] = members

    def score(members):
        pts = [{"e": e, "x": dets[e]["x"][j], "y": dets[e]["y"][j], "err": dets[e]["err"][j]} for e, j in members.items()]
        f = _fit_pts(pts, np.asarray(times))
        chi = f["chi2_red"] if f else math.inf
        return (chi > MAX_CHI2_RED, -len(members), chi)

    ranked = sorted(proposals.values(), key=score)
    used = [set() for _ in range(E)]
    tracks = []
    for members in ranked:
        free = {e: j for e, j in members.items() if j not in used[e]}
        if len(free) < MIN_EPOCHS:
            continue
        for e, j in free.items():
            used[e].add(j)
        tracks.append(dict(sorted(free.items())))
    return tracks


def _calibrate(tracks: list[list[dict]], times: np.ndarray) -> tuple[float, float, str]:
    """Position error model err^2 = (scale * stat)^2 + floor^2, fitted on this field.

    Almost every track is a still star, and a correct model makes each track's chi2 sit at the
    median expected for its degrees of freedom (0.69 per dof for 3 epochs, not 1). A single factor
    would be set by the bright stars, whose errors are the floor, and would wrongly shrink or
    inflate the faint ones; so the bright and faint halves are matched separately.
    """
    rows = [p for p in tracks if 2 * len(p) - 4 > 0]
    if len(rows) < CALIB_MIN_TRACKS:
        return 1.0, POS_FLOOR_PX, "default"
    K, L = len(rows), max(len(p) for p in rows)
    T, X, Y, S, M = (np.zeros((K, L)) for _ in range(5))
    S += 1.0
    for k, pts in enumerate(rows):
        for m, p in enumerate(pts):
            T[k, m], X[k, m], Y[k, m], S[k, m], M[k, m] = times[p["e"]], p["x"], p["y"], p["stat"], 1.0
    dof = 2 * M.sum(axis=1) - 4
    expected = chi2_dist.median(dof)
    snr = np.array([np.median([p["snr"] for p in pts]) for pts in rows])

    def norm_chi2(a, f):
        w = M / ((a * S) ** 2 + f ** 2 + 1e-12)
        sw = w.sum(axis=1)
        tm = (w * T).sum(axis=1) / sw
        dt = T - tm[:, None]
        stt = np.maximum((w * dt * dt).sum(axis=1), 1e-12)
        c = 0.0
        for V in (X, Y):
            mean = (w * V).sum(axis=1) / sw
            slope = (w * dt * V).sum(axis=1) / stt
            c = c + (w * (V - mean[:, None] - slope[:, None] * dt) ** 2).sum(axis=1)
        return c / expected

    if K < CALIB_TWO_TERM:
        k = float(np.clip(math.sqrt(float(np.median(norm_chi2(1.0, POS_FLOOR_PX)))), 0.3, 3.0))
        return k, k * POS_FLOOR_PX, "single"
    bright = snr >= np.median(snr)
    best, best_obj = (1.0, POS_FLOOR_PX), math.inf
    for a in np.geomspace(0.2, 5.0, 41):
        for f in np.concatenate([[0.0], np.geomspace(0.005, 0.5, 30)]):
            c = norm_chi2(a, f)
            obj = (math.log(float(np.median(c[bright]))) ** 2 + math.log(float(np.median(c[~bright]))) ** 2
                   + 0.01 * math.log(a) ** 2)
            if obj < best_obj:
                best, best_obj = (float(a), float(f)), obj
    return best[0], best[1], "two-term"


def _gaia_match(pts, fit, sv, gaia_px, g_v):
    """(Gaia index or None, status). Every Gaia star is carried to every epoch and compared with
    the track as a whole, so the distance and the star always refer to the same object."""
    D = np.stack([np.hypot(gaia_px[p["e"]][0] - p["x"], gaia_px[p["e"]][1] - p["y"]) for p in pts])
    med, far = np.median(D, axis=0), D.max(axis=0)
    near = np.flatnonzero(med <= GAIA_MATCH_PX)
    if not len(near):
        return None, "candidate"
    dv = np.hypot(g_v[near, 0] - fit["vx"], g_v[near, 1] - fit["vy"])
    # 3.5 sigma in 2D, plus 10% of the Gaia motion and 0.05 px/yr for systematics (parallax, scale)
    tol = np.sqrt((3.5 * sv) ** 2 + (0.1 * np.hypot(g_v[near, 0], g_v[near, 1])) ** 2 + 0.05 ** 2)
    agree = near[dv <= tol]
    if len(agree):
        return int(agree[np.argmin(med[agree])]), "known"
    # A Gaia star sits on the track at every epoch but does not move like it: the measured motion
    # is probably an artifact (blend, edge, saturation), not a discovery.
    stay = near[far[near] <= GAIA_MATCH_PX]
    if len(stay):
        return int(stay[np.argmin(med[stay])]), "inconsistent"
    return None, "candidate"


_ORDER = {"candidate": 0, "unchecked": 1, "known": 2, "doubtful": 3}


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
        times.append(_epoch_time(ep, [metas[i] for i in idx], [variances[i] for i in idx]))
    times_a = np.array(times)
    E = len(dets)
    grid, n = make_grid(job.ra, job.dec, job.fov, scale)

    gaia_rows, gaia_ok = [], True
    try:
        # the whole square grid (half diagonal), deep enough for the faint stars we detect
        gaia_rows = gaia.cone(job.ra, job.dec, job.fov / math.sqrt(2) + 0.005, max_rows=GAIA_MAX_ROWS,
                              margin_deg=GAIA_MARGIN_DEG)["sources"]
    except UpstreamError:
        gaia_ok = False
    if not gaia_rows:
        gaia_ok = False  # every field this size has Gaia stars: an empty answer is a failed query
    gaia_truncated = len(gaia_rows) >= GAIA_MAX_ROWS
    g_ra = np.array([g["ra"] for g in gaia_rows], float)
    g_dec = np.array([g["dec"] for g in gaia_rows], float)
    g_pmra = np.array([g.get("pmra") or 0.0 for g in gaia_rows], float)
    g_pmdec = np.array([g.get("pmdec") or 0.0 for g in gaia_rows], float)
    g_v = np.column_stack([-g_pmra / 1000.0 / scale, g_pmdec / 1000.0 / scale]) if len(gaia_rows) else np.zeros((0, 2))

    def gaia_px_at(year):
        dt = year - gaia.GAIA_EPOCH
        dec = g_dec + g_pmdec * dt / 3.6e6
        ra = g_ra + g_pmra * dt / 3.6e6 / np.cos(np.radians(g_dec))
        return grid.wcs_world2pix(ra, dec, 0) if len(ra) else (np.zeros(0), np.zeros(0))

    gaia_px = {e: gaia_px_at(times[e]) for e in range(E)}

    # Tracks, then forced measurements in the epochs each track missed (predicted from its fit).
    def point(e, j):
        return {"e": e, "j": j, **{k: dets[e][k][j] for k in _FIELDS}}

    tracks = link(dets, times, scale)
    trees = [cKDTree(np.column_stack([d["x"], d["y"]])) if len(d["x"]) else None for d in dets]
    used = [set() for _ in range(E)]
    for t in tracks:
        for e, j in t.items():
            used[e].add(j)
    track_pts = []
    for members in tracks:
        pts = [point(e, j) for e, j in members.items()]
        fit = _fit_pts(pts, times_a)
        if not fit:
            continue
        flux_ref = float(np.median([p["flux"] for p in pts]))
        n_cov = len(members)  # epochs whose data covers the track
        for e in range(E):
            if e in members:
                continue
            dt = times[e] - fit["t_mid"]
            px, py = fit["x"] + fit["vx"] * dt, fit["y"] + fit["vy"] * dt
            n_cov += _covered(dets[e], px, py)
            if trees[e] is not None:
                dd, jj = trees[e].query([px, py])
                if dd <= SAME_DET_PX:
                    # a detection is already there: take it if it is free and looks like the source
                    if int(jj) not in used[e] and _flux_ok(dets[e]["flux"][jj], flux_ref):
                        used[e].add(int(jj))
                        pts.append(point(e, int(jj)))
                    continue
            got = measure_at(dets[e], px, py)
            if not got or not _flux_ok(got["flux"], flux_ref):
                continue
            if trees[e] is not None:
                dd, jj = trees[e].query([got["x"], got["y"]])
                if dd <= SAME_DET_PX and int(jj) in used[e]:
                    continue  # the forced centroid slid onto a source that belongs to another track
            pts.append({"e": e, **got, "forced": True})
        pts.sort(key=lambda p: p["e"])
        track_pts.append((pts, n_cov))

    # Errors calibrated on this field, then one outlier per track may go (a wrong forced point, a
    # blend in one epoch), as long as MIN_EPOCHS remain.
    a_err, floor, how = _calibrate([p for p, _ in track_pts], times_a)
    fitted = []
    for pts, n_cov in track_pts:
        for p in pts:
            p["cal"] = math.hypot(a_err * p["stat"], max(floor, SAT_FLOOR_PX if p["sat"] else MIN_FLOOR_PX))
        fit = _fit_pts(pts, times_a, "cal")
        if not fit:
            continue
        worst = int(np.argmax(fit["norm_res"]))
        if fit["norm_res"][worst] > OUTLIER_SIGMA and len(pts) > MIN_EPOCHS:
            pts = pts[:worst] + pts[worst + 1:]
            fit = _fit_pts(pts, times_a, "cal")
            if not fit:
                continue
        fitted.append((pts, fit, n_cov))

    movers, tracks_dbg = [], []
    for pts, fit, n_cov in fitted:
        e_ix = np.array([p["e"] for p in pts])
        v = math.hypot(fit["vx"], fit["vy"])
        sv = fit["svx"]  # same on both axes: errors are circular
        signif = v / sv if sv > 0 else 0.0
        chi2 = fit["chi2_red"]
        pm_arcsec = v * scale
        baseline = float(times_a[e_ix].max() - times_a[e_ix].min())
        snr_med = float(np.median([p["snr"] for p in pts]))
        if debug:
            cx, cy = (float(v_) for v_ in grid.wcs_pix2world([[fit["x"], fit["y"]]], 0)[0])
            tracks_dbg.append({"ra": cx, "dec": cy, "pm_arcsec_yr": pm_arcsec, "significance": signif,
                               "chi2_red": chi2, "n": len(pts), "forced": sum(1 for p in pts if p.get("forced")),
                               "baseline_yr": baseline, "snr": snr_med, "err_px": float(np.median([p["cal"] for p in pts]))})
        if not (signif >= MIN_SIGNIFICANCE and pm_arcsec >= MIN_PM_ARCSEC_YR and chi2 <= MAX_CHI2_RED
                and v * baseline >= 0.5):
            continue
        # Quality: within the searched speed, at least MIN_EPOCHS real detections, seen in most epochs
        # that cover it, and a motion that does not rest on one point.
        n_det = sum(1 for p in pts if not p.get("forced"))
        if pm_arcsec > MAX_PM_ARCSEC_YR or n_det < MIN_EPOCHS or len(pts) < SEEN_FRACTION * n_cov:
            continue
        if len(pts) > MIN_EPOCHS and _jackknife_min(pts, times_a) < JACKKNIFE_SIGMA:
            continue
        match, gaia_info, reason = None, None, None
        if not gaia_ok:
            status = "unchecked"
        else:
            match, status = _gaia_match(pts, fit, sv, gaia_px, g_v)
        if status == "inconsistent":
            status, reason = "doubtful", "gaia_motion"  # a Gaia star on the track moves differently
        elif status != "known" and any(p["sat"] for p in pts):
            status, reason = "doubtful", "saturated"    # a saturated centroid can wander between epochs
        if match is not None:
            g = gaia_rows[match]
            gaia_info = {"source_id": g["source_id"], "name": g.get("name"),
                         "pm_arcsec_yr": math.hypot(g.get("pmra") or 0.0, g.get("pmdec") or 0.0) / 1000.0,
                         "g_mag": g.get("phot_g_mean_mag")}
        ra_mid, dec_mid = (float(v_) for v_ in grid.wcs_pix2world([[fit["x"], fit["y"]]], 0)[0])
        pos = []
        for p in pts:
            r_, d_ = (float(v_) for v_ in grid.wcs_pix2world([[p["x"], p["y"]]], 0)[0])
            pos.append({"epoch": int(epochs[p["e"]]["index"]), "year": times[p["e"]], "ra": r_, "dec": d_,
                        "forced": bool(p.get("forced"))})
        movers.append({
            "status": status, "reason": reason,
            "ra": ra_mid, "dec": dec_mid, "year_mid": fit["t_mid"],
            # grid x grows to the west (east is left), y to the north
            "pmra_mas_yr": -fit["vx"] * scale * 1000.0, "pmdec_mas_yr": fit["vy"] * scale * 1000.0,
            "pm_arcsec_yr": pm_arcsec, "pm_err_arcsec_yr": sv * scale, "significance": signif, "chi2_red": chi2,
            "n_epochs": len(pts), "n_forced": sum(1 for p in pts if p.get("forced")), "snr": snr_med,
            "saturated": bool(any(p["sat"] for p in pts)), "gaia": gaia_info, "positions": pos,
        })
    movers.sort(key=lambda m: (_ORDER[m["status"]], -m["significance"]))
    extra = {"tracks": tracks_dbg, "detections_per_epoch": [len(d["x"]) for d in dets], "years": times} if debug else {}
    return {**extra, "movers": movers, "n_epochs": len(epochs), "n_tracks": len(fitted),
            "n_sources": int(np.median([len(d["x"]) for d in dets])), "gaia_checked": gaia_ok,
            "gaia_truncated": gaia_truncated, "gaia_rows": len(gaia_rows),
            "error_model": {"scale": a_err, "floor_px": max(floor, MIN_FLOOR_PX), "fitted_floor_px": floor, "method": how}}
