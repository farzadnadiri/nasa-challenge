"""Gaia DR3 sources around a position, for the proper-motion overlay, with SIMBAD names."""
from __future__ import annotations

import re
import time

import httpx

from . import config
from .irsa import UpstreamError

GAIA_EPOCH = 2016.0
MAX_PM_MARGIN_DEG = 0.06  # Barnard's Star moves ~0.03 deg between 2016 and 2026
ESA_RETRY_S = 900         # after the ESA archive fails, go straight to VizieR for this long
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


def _vizier_cone(ra: float, dec: float, radius_deg: float, max_rows: int) -> list[dict]:
    """Gaia DR3 (I/355/gaiadr3, positions at epoch 2016.0) from the CfA VizieR mirror."""
    params = {"-source": "I/355/gaiadr3", "-c": f"{ra:.6f} {dec:+.6f}", "-c.rd": f"{radius_deg:.5f}",
              "-out": "Source,RA_ICRS,DE_ICRS,pmRA,pmDE,Plx,Gmag,BP-RP", "-out.max": str(max_rows), "-sort": "Gmag"}
    try:
        r = httpx.get(config.VIZIER_ASU, params=params, timeout=60)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise UpstreamError(f"Gaia archive unavailable (ESA and VizieR): {e}")
    lines = [ln for ln in r.text.splitlines() if ln and not ln.startswith("#")]
    if not lines or not lines[0].startswith("Source"):
        # an error page or a truncated answer: never read it as "no stars here"
        raise UpstreamError("Gaia archive unavailable (ESA and VizieR): VizieR returned no table")
    out = []
    for ln in lines[3:]:  # header, units, dashes
        f = [x.strip() for x in ln.split("\t")]
        if len(f) < 8 or not f[0].isdigit():
            continue
        num = lambda v: float(v) if v not in ("", "--") else None  # noqa: E731
        out.append({"source_id": f[0], "ra": float(f[1]), "dec": float(f[2]), "pmra": num(f[3]), "pmdec": num(f[4]),
                    "parallax": num(f[5]), "phot_g_mean_mag": num(f[6]), "bp_rp": num(f[7])})
    return out


_esa_down_until = 0.0


class _EsaSkipped(Exception):
    """ESA failed recently; go straight to VizieR."""


def cone(ra: float, dec: float, radius_deg: float, max_rows: int = 3000, margin_deg: float = MAX_PM_MARGIN_DEG) -> dict:
    r = radius_deg + margin_deg
    adql = f"""
SELECT TOP {max_rows} source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag, bp_rp
FROM gaiadr3.gaia_source
WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.7f}, {dec:.7f}, {r:.5f}))
ORDER BY phot_g_mean_mag ASC
"""
    global _esa_down_until
    try:
        if time.time() < _esa_down_until:
            raise _EsaSkipped()  # skip the 8-20 s wait for it
        resp = httpx.post(config.GAIA_TAP, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": adql},
                          timeout=httpx.Timeout(connect=8, read=60, write=10, pool=10))
        resp.raise_for_status()
        d = resp.json()
        cols = [c["name"] for c in d["metadata"]]
        rows = [dict(zip(cols, row)) for row in d["data"]]
    except _EsaSkipped:
        rows = _vizier_cone(ra, dec, r, max_rows)
    except (httpx.HTTPError, ValueError, KeyError):
        _esa_down_until = time.time() + ESA_RETRY_S
        rows = _vizier_cone(ra, dec, r, max_rows)  # ESA archive down: same catalogue from VizieR
    for row in rows:
        row["source_id"] = str(row["source_id"])  # 64-bit ints lose precision in JS
    names = simbad_names([row["source_id"] for row in rows[:NAMED_SOURCES]])
    for row in rows:
        if row["source_id"] in names:
            row.update(names[row["source_id"]])
    return {"ref_epoch": GAIA_EPOCH, "sources": rows, "named": len(names)}
