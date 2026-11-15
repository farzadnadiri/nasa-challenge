"""User-uploaded datasets: one FITS file per exposure, following the "FITS + WCS v1" standard.

Standard (also in README and shown in the app):
  * FITS file (.fits / .fit / .fts, optionally .gz), one exposure per file.
  * Image: the first 2-D image HDU; an extension named IMAGE or SCI is preferred.
  * Required: a celestial WCS (RA/Dec) and an observation time: MJD-OBS, MJD-AVG, MJD or DATE-OBS
    (ISO, UTC) in the image header or the primary header.
  * Optional: a noise extension VARIANCE (variance), ERR/SIGMA/UNCERT (1-sigma) or WEIGHT/IVAR
    (inverse variance); a bad-pixel extension MASK, FLAGS or DQ (non-zero = bad); WAVELEN (micron)
    or FILTER; BUNIT for the flux unit.
  * Limits: up to 50 files, 100 MB each, 1 GB per dataset, images up to 6000 x 6000 pixels.
"""
from __future__ import annotations

import gzip
import io
import re
import time
import warnings

import httpx
import numpy as np
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS, FITSFixedWarning
from astropy.wcs.utils import proj_plane_pixel_scales

from . import config

warnings.filterwarnings("ignore", category=FITSFixedWarning)

DATASET_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_PIXELS = 6000 * 6000
IMAGE_NAMES = ("IMAGE", "SCI", "PRIMARY")
VAR_NAMES = ("VARIANCE", "VAR")
SIGMA_NAMES = ("ERR", "ERROR", "SIGMA", "UNCERT")
IVAR_NAMES = ("WEIGHT", "WHT", "IVAR")
MASK_NAMES = ("MASK", "FLAGS", "DQ", "BPM")

_client = httpx.Client(timeout=120, follow_redirects=True, headers=config.IRSA_HEADERS)


class UploadError(ValueError):
    """A file or dataset that does not follow the standard; message is shown to the user."""


def _open(raw: bytes) -> fits.HDUList:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    if not raw.startswith(b"SIMPLE  ="):
        raise UploadError("not a FITS file")
    return fits.open(io.BytesIO(raw), memmap=False)


def _image_hdu(h: fits.HDUList):
    images = [x for x in h if x.is_image and x.data is not None and x.data.ndim == 2]
    if not images:
        raise UploadError("no 2-D image found")
    for name in IMAGE_NAMES:
        for x in images:
            if x.name == name:
                return x
    return images[0]


def _find(h: fits.HDUList, names: tuple[str, ...], shape) -> np.ndarray | None:
    for x in h:
        if x.name in names and x.data is not None and x.data.shape == shape:
            return np.asarray(x.data, dtype=np.float32)
    return None


def _obs_time(hdr: fits.Header, prim: fits.Header) -> float:
    for h in (hdr, prim):
        for k in ("MJD-AVG", "MJD-OBS", "MJD"):
            v = h.get(k)
            if isinstance(v, (int, float)) and 30000 < v < 100000:
                return float(v)
        for k in ("DATE-AVG", "DATE-OBS"):
            v = h.get(k)
            if isinstance(v, str) and v.strip():
                s = v.strip()
                if "T" not in s and h.get("TIME-OBS"):
                    s = f"{s}T{str(h['TIME-OBS']).strip()}"
                try:
                    return float(Time(s, scale="utc").mjd)
                except ValueError:
                    continue
    raise UploadError("no observation time (MJD-OBS, MJD-AVG or DATE-OBS)")


def read_upload(raw: bytes, name: str) -> dict:
    """Parse one uploaded file. Returns arrays, WCS and metadata, or raises UploadError."""
    try:
        h = _open(raw)
    except UploadError:
        raise
    except Exception as e:  # corrupt or truncated FITS
        raise UploadError(f"unreadable FITS ({type(e).__name__})")
    with h:
        img_hdu = _image_hdu(h)
        img = np.asarray(img_hdu.data, dtype=np.float32)
        if img.size > MAX_PIXELS:
            raise UploadError(f"image is {img.shape[1]} x {img.shape[0]}; the limit is 6000 x 6000")
        hdr, prim = img_hdu.header, h[0].header
        try:
            wcs = WCS(hdr).celestial
        except Exception:
            wcs = None
        if wcs is None or not wcs.has_celestial or wcs.naxis != 2:
            raise UploadError("no celestial WCS (RA/Dec) in the image header")
        mjd = _obs_time(hdr, prim)

        var = _find(h, VAR_NAMES, img.shape)
        if var is None and (sig := _find(h, SIGMA_NAMES, img.shape)) is not None:
            var = sig ** 2
        if var is None and (ivar := _find(h, IVAR_NAMES, img.shape)) is not None:
            with np.errstate(divide="ignore"):
                var = np.where(ivar > 0, 1.0 / ivar, np.nan).astype(np.float32)
        mask = _find(h, MASK_NAMES, img.shape)
        bad = (mask != 0) if mask is not None else np.zeros(img.shape, bool)

        wl = None
        for h_ in (hdr, prim):
            for k in ("WAVELEN", "WAVELENG", "LAMBDA"):
                if isinstance(h_.get(k), (int, float)) and h_[k] > 0:
                    wl = float(h_[k])
                    break
            if wl:
                break
        scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
        ny, nx = img.shape
        c_ra, c_dec = (float(v) for v in wcs.pixel_to_world_values((nx - 1) / 2, (ny - 1) / 2))
        extent = min(nx, ny) * scale / 3600.0
    return {
        "name": name, "image": img, "variance": var, "bad": bad, "wcs": wcs, "mjd": mjd,
        "wavelength_um": wl, "filter": str(hdr.get("FILTER", prim.get("FILTER", ""))).strip(),
        "bunit": str(hdr.get("BUNIT", prim.get("BUNIT", ""))).strip(),
        "pixel_scale_arcsec": scale, "center": (c_ra, c_dec), "extent_deg": extent, "shape": [ny, nx],
    }


def fetch(dataset: str, path: str) -> bytes:
    """Read an uploaded object through the Worker's internal route (needs the shared proxy key)."""
    r = _client.get(f"{config.UPLOAD_BASE}{dataset}/{path}")
    if r.status_code != 200:
        raise UploadError(f"could not read {path} (HTTP {r.status_code})")
    return r.content


def manifest(dataset: str) -> dict:
    if not DATASET_ID_RE.match(dataset):
        raise UploadError("invalid dataset id")
    import json
    return json.loads(fetch(dataset, "manifest.json"))


def plan(files: list[dict], ra: float | None, dec: float | None) -> dict:
    """Choose the target, pixel scale and field of view for a set of parsed files."""
    if ra is None or dec is None:
        # Median, not mean: one stray image elsewhere on the sky must not drag the centre away.
        # RA is unwrapped around the first file so datasets straddling 0h work.
        ref = files[0]["center"][0]
        dra = [((f["center"][0] - ref + 180.0) % 360.0) - 180.0 for f in files]
        ra = float((ref + np.median(dra)) % 360.0)
        dec = float(np.median([f["center"][1] for f in files]))
    scale = float(np.median([f["pixel_scale_arcsec"] for f in files]))
    fov = float(min(np.median([f["extent_deg"] for f in files]) * 0.9, config.MAX_FOV_DEG))
    # Keep the processing grid to a sane size whatever the input resolution.
    scale = max(scale, fov * 3600.0 / 401)
    return {"ra": ra, "dec": dec, "pixel_scale_arcsec": scale, "fov": fov}


def wait_for_manifest(dataset: str, tries: int = 3) -> dict:
    last = None
    for _ in range(tries):
        try:
            return manifest(dataset)
        except UploadError as e:
            last = e
            time.sleep(1)
    raise last  # type: ignore[misc]
