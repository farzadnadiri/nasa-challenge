"""Gaia DR3 sources around a position, for the proper-motion overlay."""
from __future__ import annotations

import httpx

from . import config
from .irsa import UpstreamError

GAIA_EPOCH = 2016.0
MAX_PM_MARGIN_DEG = 0.06  # Barnard's Star moves ~0.03 deg between 2016 and 2026


def cone(ra: float, dec: float, radius_deg: float, max_rows: int = 3000) -> dict:
    r = radius_deg + MAX_PM_MARGIN_DEG
    adql = f"""
SELECT TOP {max_rows} source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag
FROM gaiadr3.gaia_source
WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.7f}, {dec:.7f}, {r:.5f}))
ORDER BY phot_g_mean_mag ASC
"""
    try:
        resp = httpx.post(config.GAIA_TAP, data={"REQUEST": "doQuery", "LANG": "ADQL",
                                                 "FORMAT": "json", "QUERY": adql}, timeout=90)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise UpstreamError(f"Gaia archive unavailable: {e}")
    d = resp.json()
    cols = [c["name"] for c in d["metadata"]]
    rows = [dict(zip(cols, row)) for row in d["data"]]
    for row in rows:
        row["source_id"] = str(row["source_id"])  # 64-bit ints lose precision in JS
    return {"ref_epoch": GAIA_EPOCH, "sources": rows}
