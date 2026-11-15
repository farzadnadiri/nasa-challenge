"""Gaia DR3 sources around a position, for the proper-motion overlay, with SIMBAD names."""
from __future__ import annotations

import re

import httpx

from . import config
from .irsa import UpstreamError

GAIA_EPOCH = 2016.0
MAX_PM_MARGIN_DEG = 0.06  # Barnard's Star moves ~0.03 deg between 2016 and 2026
NAMED_SOURCES = 300       # SIMBAD names are looked up for the brightest sources only

# SIMBAD main identifiers carry catalogue prefixes; drop the generic ones for display.
_NAME_PREFIX = re.compile(r"^(NAME|\*\*|\*|V\*|EM\*|PM\*)\s+")


def _display_name(main_id: str) -> str:
    return re.sub(r"\s+", " ", _NAME_PREFIX.sub("", main_id.strip()))


def simbad_names(source_ids: list[str]) -> dict[str, dict]:
    """{gaia source_id: {'name', 'otype'}} for stars SIMBAD knows, in one query. Best effort:
    an unavailable SIMBAD just means no names."""
    if not source_ids:
        return {}
    ids = ", ".join(f"'Gaia DR3 {sid}'" for sid in source_ids if sid.isdigit())
    adql = f"SELECT i.id, b.main_id, b.otype FROM ident AS i JOIN basic AS b ON b.oid = i.oidref WHERE i.id IN ({ids})"
    try:
        r = httpx.post(config.SIMBAD_TAP, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json",
                                               "QUERY": adql}, timeout=20)
        r.raise_for_status()
        rows = r.json()["data"]
    except (httpx.HTTPError, ValueError, KeyError):
        return {}
    out = {}
    for ident, main_id, otype in rows:
        sid = str(ident).replace("Gaia DR3", "").strip()
        out[sid] = {"name": _display_name(str(main_id)), "otype": str(otype or "")}
    return out


def cone(ra: float, dec: float, radius_deg: float, max_rows: int = 3000) -> dict:
    r = radius_deg + MAX_PM_MARGIN_DEG
    adql = f"""
SELECT TOP {max_rows} source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag, bp_rp
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
    names = simbad_names([row["source_id"] for row in rows[:NAMED_SOURCES]])
    for row in rows:
        if row["source_id"] in names:
            row.update(names[row["source_id"]])
    return {"ref_epoch": GAIA_EPOCH, "sources": rows, "named": len(names)}
