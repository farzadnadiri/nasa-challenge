"""Download one SPHEREx cutout and turn it into a clean frame on the shared sky grid."""
from __future__ import annotations

import io
import logging
import time
import warnings

import httpx
import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from astropy.wcs import WCS, FITSFixedWarning
from reproject import reproject_interp

from . import config
from . import s3reader
from .irsa import Exposure

logging.getLogger("astropy").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=FITSFixedWarning)

_client = httpx.Client(timeout=120, follow_redirects=True, headers=config.IRSA_HEADERS,
                       limits=httpx.Limits(max_connections=config.FETCH_WORKERS * 2))
_s3 = httpx.Client(timeout=60, limits=httpx.Limits(max_connections=config.FETCH_WORKERS * 2,
                                                   max_keepalive_connections=config.FETCH_WORKERS * 2))


def make_grid(ra: float, dec: float, fov_deg: float, scale_arcsec: float = config.PIXEL_SCALE_ARCSEC) -> tuple[WCS, int]:
    """North-up, east-left TAN grid (native SPHEREx pixels by default), odd size so the target is central."""
    scale = scale_arcsec / 3600.0
    n = int(np.ceil(fov_deg / scale))
    n += 1 - n % 2
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    w.wcs.cdelt = [-scale, scale]
    return w, n


def fetch_cutout(exp: Exposure, ra: float, dec: float, size_deg: float, retries: int = 3) -> fits.HDUList:
    url = f"{config.IRSA_BASE}{exp.uri}?center={ra:.7f},{dec:.7f}d&size={size_deg:.4f}"
    last = None
    for attempt in range(retries):
        try:
            r = _client.get(url)
            if r.status_code == 200:
                return fits.open(io.BytesIO(r.content), memmap=False)
            last = f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            last = str(e)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"cutout failed for {exp.obsid} D{exp.detector}: {last}")


def _flag_bits(flags_header, names: set[str]) -> int:
    mask = 0
    for key in flags_header["MSKN*"]:
        if str(flags_header[key]).strip() in names:
            mask |= 1 << int(key[4:])
    return mask


def _flag_names(flags_header, value: int) -> list[str]:
    out = []
    for key in flags_header["MSKN*"]:
        if value & (1 << int(key[4:])):
            out.append(str(flags_header[key]).strip())
    return out


def _load_s3(exp: Exposure, ra: float, dec: float, size: float) -> dict:
    """Fast path: only the needed IMAGE/FLAGS rows straight from the public S3 bucket."""
    key = exp.uri.split("spherex/", 1)[1]
    s = s3reader.read_section(_s3, key, ra, dec, size)
    hdr, img = s["header"], s["image"]
    h, w = img.shape
    return {
        "hdr": hdr, "img": img, "flags": s["flags"], "fhdr": s["flags_header"],
        "var": None, "zodi": None,
        "wcs": WCS(hdr)[s["y0"]:s["y0"] + h, s["x0"]:s["x0"] + w],
        "spec": (s["wcs_wave_hdul"], s["x0"], s["y0"]),  # spectral WCS uses full-frame pixels
        "version": str(s["primary_header"].get("VERSION", "")).strip(),
    }


def _load_irsa(exp: Exposure, ra: float, dec: float, size: float) -> dict:
    """Fallback: IRSA cutout service (~4 MB per request, slower)."""
    with fetch_cutout(exp, ra, dec, size) as h:
        hdr = h["IMAGE"].header
        return {
            "hdr": hdr, "img": h["IMAGE"].data.astype(np.float32), "flags": h["FLAGS"].data,
            "fhdr": h["FLAGS"].header, "var": h["VARIANCE"].data.astype(np.float32),
            "zodi": h["ZODI"].data.astype(np.float32), "wcs": WCS(hdr),
            "spec": (fits.HDUList([fits.PrimaryHDU(), h["WCS-WAVE"].copy()]), 0, 0),
            "version": str(h[0].header.get("VERSION", "")).strip(),
        }


def process_exposure(exp: Exposure, ra: float, dec: float, fov_deg: float,
                     grid: WCS, n: int) -> dict:
    """Returns {'image','variance' (n x n float32 on grid), 'meta' (dict)}."""
    # Grid rotation relative to the detector means corners need ~sqrt(2) margin.
    size = fov_deg * 1.45 + 0.01
    try:
        d = _load_s3(exp, ra, dec, size)
        source = "s3"
    except Exception:
        d = _load_irsa(exp, ra, dec, size)
        source = "irsa"
    hdr, img, flags, fhdr, wcs = d["hdr"], d["img"], d["flags"], d["fhdr"], d["wcs"]

    x, y = (float(v) for v in wcs.world_to_pixel_values(ra, dec))
    xi, yi = int(round(x)), int(round(y))
    inside = 0 <= xi < img.shape[1] and 0 <= yi < img.shape[0]

    wavelength = bandwidth = None
    try:
        spec_hdul, x0, y0 = d["spec"]
        sw = WCS(hdr, fobj=spec_hdul, key="W")
        sw.sip = None
        wl, bw = sw.pixel_to_world(x + x0, y + y0)
        wavelength, bandwidth = float(wl.to_value("um")), float(bw.to_value("um"))
    except Exception:  # spectral WCS lookup table missing or unreadable
        pass

    target_flags = _flag_names(fhdr, int(flags[yi, xi])) if inside else []
    meta = {
        "obsid": exp.obsid,
        "detector": exp.detector,
        "epoch": exp.epoch,
        "mjd": float(hdr.get("MJD-AVG", exp.mjd)),
        "date_obs": str(hdr.get("DATE-OBS", "")),
        "wavelength_um": wavelength,
        "bandwidth_um": bandwidth,
        "psf_fwhm_arcsec": float(hdr.get("PSF_FWHM", np.nan)),
        "version": d["version"],
        "release": exp.release,
        "source": source,
        "target_on_image": bool(inside),
        "target_flags": target_flags,
        "contaminated": bool(set(target_flags) & config.CONTAMINATION_FLAG_NAMES),
        "saturated": bool(set(target_flags) & config.SATURATION_FLAG_NAMES),
    }

    bad = (flags & _flag_bits(fhdr, config.BAD_FLAG_NAMES)) != 0
    data = img - d["zodi"] if d["zodi"] is not None else img.copy()
    img_r, var_r = clean_and_reproject(data, d["var"], bad, wcs, grid, n)
    # Pixels the pipeline flags as light from something else (ghost, streak, halo, crosstalk...).
    cont = ((flags & _flag_bits(fhdr, config.CONTAMINATION_FLAG_NAMES)) != 0).astype(np.float32)
    cont_r, _ = reproject_interp((cont, wcs), grid, shape_out=(n, n), order="nearest-neighbor")
    return {"image": img_r, "variance": var_r, "contam": (np.nan_to_num(cont_r) > 0.5).astype(np.uint8), "meta": meta}


def clean_and_reproject(data: np.ndarray, var: np.ndarray | None, bad: np.ndarray, wcs: WCS,
                        grid: WCS, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Mask bad pixels, remove the local sky level and resample onto the shared grid.
    Used for SPHEREx exposures and for uploaded files alike."""
    data = data.astype(np.float32, copy=True)
    data[bad | ~np.isfinite(data)] = np.nan
    # Remove the remaining local sky level (zodiacal light differs between epochs).
    finite = data[np.isfinite(data)]
    sky_sigma = np.nan
    if finite.size > 10:
        _, med, sky_sigma = sigma_clipped_stats(finite, sigma=3.0, maxiters=5)
        data -= np.float32(med)
    if var is None:
        # S3 path skips the VARIANCE plane: use the frame's robust sky noise as a uniform
        # variance. Good for weighting the stack; photometry errors on bright stars are optimistic.
        var = np.full(data.shape, np.float32(sky_sigma ** 2 if np.isfinite(sky_sigma) and sky_sigma > 0 else np.nan))
    var = var.astype(np.float32)
    data[~(var > 0)] = np.nan
    var[~np.isfinite(data)] = np.nan

    img_r, _ = reproject_interp((data, wcs), grid, shape_out=(n, n), order="bilinear")
    var_r, _ = reproject_interp((var, wcs), grid, shape_out=(n, n), order="bilinear")
    return img_r.astype(np.float32), var_r.astype(np.float32)
