"""CatWISE2020 (WISE mission catalogue with proper motions) around a position, for the overlay.

Infrared (3.4 and 4.6 micron), so it lists red objects that Gaia misses: a source with a red
W1 - W2 colour and real motion is the classic brown-dwarf signature. Positions refer to MJD 57170
(2015.4); proper motions are in arcsec/yr, converted to mas/yr here.
"""
from __future__ import annotations

import httpx

from . import config
from .irsa import UpstreamError

CATWISE_EPOCH = 2015.4   # MJD 57170, the CatWISE2020 reference epoch
PM_MARGIN_DEG = 0.06     # same allowance for fast movers as for Gaia


def cone(ra: float, dec: float, radius_deg: float, max_rows: int = 2000) -> dict:
    # This VizieR mirror returns nothing for CatWISE when columns are chosen with -out, so all
    # columns are requested and the needed ones are picked by name from the header row.
    params = {"-source": "II/365/catwise", "-c": f"{ra:.6f} {dec:+.6f}",
              "-c.rs": f"{(radius_deg + PM_MARGIN_DEG) * 3600:.0f}", "-out.max": str(max_rows), "-out.all": "1"}  # "-out.all=" (empty) returns nothing
    try:
        r = httpx.get(config.VIZIER_ASU, params=params, timeout=90)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise UpstreamError(f"CatWISE (VizieR) unavailable: {e}")
    lines = [ln for ln in r.text.splitlines() if ln and not ln.startswith("#")]
    out = []
    if len(lines) < 3:
        return {"ref_epoch": CATWISE_EPOCH, "sources": out}
    col = {name.strip(): i for i, name in enumerate(lines[0].split("\t"))}
    need = ("Name", "RA_ICRS", "DE_ICRS", "pmRA", "pmDE", "W1mproPM", "W2mproPM")
    if not all(k in col for k in need):
        raise UpstreamError("CatWISE (VizieR) returned unexpected columns")
    num = lambda v: float(v) if v.strip() not in ("", "--") else None  # noqa: E731
    for ln in lines[3:]:  # after header, units and dashes
        f = ln.split("\t")
        try:
            pmra, pmdec = num(f[col["pmRA"]]), num(f[col["pmDE"]])
            w1, w2 = num(f[col["W1mproPM"]]), num(f[col["W2mproPM"]])
            out.append({"name": f[col["Name"]].strip(), "ra": float(f[col["RA_ICRS"]]), "dec": float(f[col["DE_ICRS"]]),
                        "pmra": pmra * 1000.0 if pmra is not None else None,     # arcsec/yr -> mas/yr
                        "pmdec": pmdec * 1000.0 if pmdec is not None else None,
                        "w1": w1, "w2": w2, "w1_w2": (w1 - w2) if w1 is not None and w2 is not None else None})
        except (ValueError, IndexError):
            continue
    out.sort(key=lambda s: s["w2"] if s["w2"] is not None else 99.0)  # brightest at 4.6 um first
    return {"ref_epoch": CATWISE_EPOCH, "sources": out}
