"""Turn user input (decimal, sexagesimal, or an object name) into a position for the data epoch."""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import astropy.units as u
import httpx
from astropy.coordinates import SkyCoord

from . import config
from .irsa import UpstreamError

# Middle of the SPHEREx data so far; names resolve to J2000-epoch positions, so fast movers
# must be carried forward or they fall outside a small field of view.
DATA_EPOCH = 2026.0
SIMBAD_EPOCH = 2000.0


def parse_coordinates(q: str) -> SkyCoord | None:
    s = q.strip()
    tokens = s.replace(",", " ").split()
    try:
        if len(tokens) == 2 and all(re.fullmatch(r"[+-]?\d+(\.\d*)?", t) for t in tokens):
            return SkyCoord(float(tokens[0]), float(tokens[1]), unit="deg")
        if re.search(r"\d", s) and (re.search(r"[:hms]", s) or len(tokens) == 6):
            return SkyCoord(s.replace(",", " "), unit=(u.hourangle, u.deg))
    except ValueError:
        return None
    return None


def propagate(ra: float, dec: float, pmra_masyr: float, pmdec_masyr: float, years: float) -> tuple[float, float]:
    """Linear proper-motion propagation (pmra includes cos(dec)); fine for decades at these speeds."""
    ddec = pmdec_masyr * years / 3.6e6
    dra = pmra_masyr * years / 3.6e6 / max(math.cos(math.radians(dec)), 1e-6)
    return (ra + dra) % 360.0, dec + ddec


def sesame(name: str) -> dict:
    try:
        r = httpx.get(f"{config.SESAME}?{quote(name)}", timeout=30, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise UpstreamError(f"Name resolver unavailable: {e}")
    root = ET.fromstring(r.text)
    res = root.find(".//Resolver[jradeg]")
    if res is None:
        raise ValueError(f"Could not resolve '{name}'")

    def f(tag, default=0.0):
        el = res.find(f".//{tag}")  # pmRA/pmDE are nested inside <pm>
        try:
            return float(el.text) if el is not None and el.text else default
        except ValueError:
            return default

    oname = res.find("oname")
    otype = res.find("otype")
    return {
        "name": oname.text if oname is not None else name,
        "otype": otype.text if otype is not None else None,
        "ra_j2000": f("jradeg"), "dec_j2000": f("jdedeg"),
        "pmra": f("pmRA"), "pmdec": f("pmDE"),
    }


def resolve(q: str) -> dict:
    c = parse_coordinates(q)
    if c is not None:
        return {"query": q, "name": None, "ra": c.ra.deg, "dec": c.dec.deg, "pmra": None, "pmdec": None,
                "note": "coordinates used as given"}
    s = sesame(q)
    ra, dec = propagate(s["ra_j2000"], s["dec_j2000"], s["pmra"], s["pmdec"], DATA_EPOCH - SIMBAD_EPOCH)
    moved = math.hypot(s["pmra"], s["pmdec"]) * (DATA_EPOCH - SIMBAD_EPOCH) / 1000.0
    return {"query": q, "name": s["name"], "otype": s["otype"], "ra": ra, "dec": dec,
            "ra_j2000": s["ra_j2000"], "dec_j2000": s["dec_j2000"], "pmra": s["pmra"], "pmdec": s["pmdec"],
            "note": (f"position carried from J2000 to {DATA_EPOCH:.1f} using proper motion "
                     f"({moved:.0f}″ shift)") if moved > 1 else "SIMBAD position"}
