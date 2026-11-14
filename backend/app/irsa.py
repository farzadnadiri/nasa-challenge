"""Find SPHEREx spectral images covering a position and group them into epochs."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, asdict

import httpx
from astropy.table import Table
from astropy.time import Time
import io

from . import config


class UpstreamError(RuntimeError):
    """IRSA (or another archive) failed; message is safe to show to users."""


@dataclass
class Exposure:
    uri: str            # path relative to IRSA_BASE, e.g. ibe/data/spherex/dr1/level2/...
    mjd: float          # exposure start (MJD)
    detector: int       # 1..6
    obsid: str          # e.g. 2025W17_4B_0001_1
    release: str        # dr1 / qr3 / ...
    epoch: int = -1     # filled by assign_epochs

    def to_dict(self):
        return asdict(self)


_OBSID_RE = re.compile(r"level2_(\d{4}W\d{2}_\w{2}_\d{4}_\d)D\d")
_RELEASE_RE = re.compile(r"spherex/(\w+?)/")


def _tap_query(adql: str, retries: int = 3) -> Table:
    deadline = time.time() + config.TAP_DEADLINE_S
    timeout = httpx.Timeout(connect=15, read=config.TAP_DEADLINE_S, write=15, pool=15)
    last = None
    for attempt in range(retries):
        try:
            r = httpx.post(
                config.IRSA_TAP,
                data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "votable", "QUERY": adql},
                timeout=timeout,
                headers=config.IRSA_HEADERS,
            )
            if r.status_code == 200:
                return Table.read(io.BytesIO(r.content), format="votable", use_names_over_ids=True)
            last = f"HTTP {r.status_code}"
        except httpx.HTTPError as e:  # network errors and timeouts
            last = type(e).__name__
        if time.time() + 2 * (attempt + 1) > deadline:
            break
        time.sleep(2 * (attempt + 1))
    raise UpstreamError(f"IRSA search service is unavailable ({last}). Try again shortly.")


def _cache_path(ra: float, dec: float, detectors: list[int]):
    key = hashlib.sha1(f"q|{ra:.4f}|{dec:.4f}|{sorted(detectors)}".encode()).hexdigest()[:16]
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CACHE_DIR / f"query_{key}.json"


def query_exposures(ra: float, dec: float, detectors: list[int]) -> list[Exposure]:
    """All spectral images whose footprint contains (ra, dec), oldest first.

    Uses TAP rather than SIA: TAP sees new data immediately after the weekly ingest.
    Results are cached on disk for CACHE_TTL_DAYS (SPHEREx ingests weekly), which also
    protects the demo from IRSA search outages.
    """
    path = _cache_path(ra, dec, detectors)
    if path.exists() and time.time() - path.stat().st_mtime < config.CACHE_TTL_DAYS * 86400:
        return [Exposure(**{**d, "epoch": -1}) for d in json.loads(path.read_text())]
    exps = _query_exposures_live(ra, dec, detectors)
    path.write_text(json.dumps([e.to_dict() for e in exps]))
    return exps


def _query_exposures_live(ra: float, dec: float, detectors: list[int]) -> list[Exposure]:
    """SIA first (fast, ~1 day ingest lag), TAP as fallback; either one can be down."""
    errors = []
    for fn in (_query_sia, _query_tap):
        try:
            return fn(ra, dec, detectors)
        except UpstreamError as e:
            errors.append(str(e))
    raise UpstreamError(" / ".join(errors))


def _query_sia(ra: float, dec: float, detectors: list[int]) -> list[Exposure]:
    params = [("COLLECTION", c) for c in config.SIA_COLLECTIONS]
    params += [("POS", f"circle {ra:.7f} {dec:.7f} 0.0003"), ("RESPONSEFORMAT", "votable")]
    try:
        r = httpx.get(config.IRSA_SIA, params=params, headers=config.IRSA_HEADERS,
                      timeout=httpx.Timeout(connect=15, read=config.TAP_DEADLINE_S, write=15, pool=15))
    except httpx.HTTPError as e:
        raise UpstreamError(f"IRSA SIA unavailable ({type(e).__name__})")
    if r.status_code != 200:
        raise UpstreamError(f"IRSA SIA unavailable (HTTP {r.status_code})")
    t = Table.read(io.BytesIO(r.content), format="votable", use_names_over_ids=True)
    wanted = {f"SPHEREx-D{d}" for d in detectors}
    out = []
    for row in t:
        band = str(row["energy_bandpassname"])
        if band not in wanted or str(row["dataproduct_subtype"]) != "science":
            continue
        out.append(_exposure(str(row["access_url"]).replace(config.IRSA_PUBLIC, ""),
                             float(row["t_min"]), int(band[-1])))
    return dedupe(out)


def _exposure(uri: str, mjd: float, det: int) -> Exposure:
    m = _OBSID_RE.search(uri)
    rel = _RELEASE_RE.search(uri)
    return Exposure(uri=uri.lstrip("/"), mjd=mjd, detector=det,
                    obsid=m.group(1) if m else uri.rsplit("/", 1)[-1], release=rel.group(1) if rel else "")


def _query_tap(ra: float, dec: float, detectors: list[int]) -> list[Exposure]:
    bands = ", ".join(f"'SPHEREx-D{d}'" for d in sorted(detectors))
    adql = f"""
SELECT a.uri, p.time_bounds_lower, p.energy_bandpassname
FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid
WHERE 1 = CONTAINS(POINT('ICRS', {ra:.7f}, {dec:.7f}), p.poly)
  AND p.energy_bandpassname IN ({bands})
ORDER BY p.time_bounds_lower
"""
    t = _tap_query(adql)
    return dedupe([_exposure(str(row["uri"]), float(row["time_bounds_lower"]),
                             int(str(row["energy_bandpassname"])[-1])) for row in t])


def dedupe(exps: list[Exposure]) -> list[Exposure]:
    """Keep one image per (obsid, detector), preferring the newest release (dr1 > qr3 > qr2)."""
    rank = {"dr1": 3, "qr3": 2, "qr2": 1}
    best: dict[tuple, Exposure] = {}
    for e in exps:
        k = (e.obsid, e.detector)
        if k not in best or rank.get(e.release, 0) > rank.get(best[k].release, 0):
            best[k] = e
    return sorted(best.values(), key=lambda e: e.mjd)


def assign_epochs(exps: list[Exposure],
                  gap: float = config.EPOCH_GAP_DAYS,
                  max_span: float = config.EPOCH_MAX_SPAN_DAYS) -> list[dict]:
    """Cluster exposures into visits. Mutates exps[i].epoch; returns epoch summaries."""
    if not exps:
        return []
    exps.sort(key=lambda e: e.mjd)
    clusters: list[list[Exposure]] = [[exps[0]]]
    for e in exps[1:]:
        if e.mjd - clusters[-1][-1].mjd > gap:
            clusters.append([e])
        else:
            clusters[-1].append(e)
    # Split long clusters (continuous coverage near the ecliptic poles) into fixed windows.
    final: list[list[Exposure]] = []
    for c in clusters:
        span = c[-1].mjd - c[0].mjd
        if span <= max_span:
            final.append(c)
            continue
        start = c[0].mjd
        cur: list[Exposure] = []
        for e in c:
            if e.mjd - start > max_span and cur:
                final.append(cur)
                cur, start = [], e.mjd
            cur.append(e)
        if cur:
            final.append(cur)
    summaries = []
    for i, c in enumerate(final):
        for e in c:
            e.epoch = i
        mjds = [e.mjd for e in c]
        mid = (min(mjds) + max(mjds)) / 2
        summaries.append({
            "index": i,
            "mjd_start": min(mjds),
            "mjd_end": max(mjds),
            "mjd_mid": mid,
            "date_mid": Time(mid, format="mjd").to_value("iso", subfmt="date"),
            "decimal_year": float(Time(mid, format="mjd").decimalyear),
            "n_exposures": len(c),
        })
    return summaries
