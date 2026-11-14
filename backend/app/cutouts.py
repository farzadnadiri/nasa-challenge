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
from .irsa import Exposure

logging.getLogger("astropy").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=FITSFixedWarning)

_client = httpx.Client(timeout=120, follow_redirects=True,
                       limits=httpx.Limits(max_connections=config.FETCH_WORKERS * 2))


def make_grid(ra: float, dec: float, fov_deg: float) -> tuple[WCS, int]:
    """North-up, east-left TAN grid at native SPHEREx pixel scale, odd size so the target is central."""
    scale = config.PIXEL_SCALE_ARCSEC / 3600.0
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


def process_exposure(exp: Exposure, ra: float, dec: float, fov_deg: float,
                     grid: WCS, n: int) -> dict:
    """Returns {'image','variance' (n x n float32 on grid), 'meta' (dict)}."""
    # Grid rotation relative to the detector means corners need ~sqrt(2) margin.
    size = fov_deg * 1.45 + 0.01
    with fetch_cutout(exp, ra, dec, size) as h:
        hdr = h["IMAGE"].header
        img = h["IMAGE"].data.astype(np.float32)
        var = h["VARIANCE"].data.astype(np.float32)
        zodi = h["ZODI"].data.astype(np.float32)
        flags = h["FLAGS"].data
        fhdr = h["FLAGS"].header

        wcs = WCS(hdr)
        x, y = wcs.world_to_pixel_values(ra, dec)
        xi, yi = int(round(float(x))), int(round(float(y)))
        inside = 0 <= xi < img.shape[1] and 0 <= yi < img.shape[0]

        wavelength = bandwidth = None
        try:
            sw = WCS(hdr, fobj=h, key="W")
            sw.sip = None
            wl, bw = sw.pixel_to_world(float(x), float(y))
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
            "version": str(h[0].header.get("VERSION", "")).strip(),
            "release": exp.release,
            "target_on_image": bool(inside),
            "target_flags": target_flags,
            "contaminated": bool(set(target_flags) & config.CONTAMINATION_FLAG_NAMES),
            "saturated": bool(set(target_flags) & config.SATURATION_FLAG_NAMES),
        }

    bad = (flags & _flag_bits(fhdr, config.BAD_FLAG_NAMES)) != 0
    data = img - zodi
    data[bad | ~np.isfinite(data) | ~(var > 0)] = np.nan
    # Remove the remaining local sky level so epochs with different zodiacal light match.
    finite = data[np.isfinite(data)]
    if finite.size > 10:
        _, med, _ = sigma_clipped_stats(finite, sigma=3.0, maxiters=5)
        data -= np.float32(med)
    var[~np.isfinite(data)] = np.nan

    img_r, _ = reproject_interp((data, wcs), grid, shape_out=(n, n), order="bilinear")
    var_r, _ = reproject_interp((var, wcs), grid, shape_out=(n, n), order="bilinear")
    return {"image": img_r.astype(np.float32), "variance": var_r.astype(np.float32), "meta": meta}
