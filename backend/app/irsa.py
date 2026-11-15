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


def _cache_path(ra: float, dec: float, detectors: list[int], collections: list[str]):
    # The release list is part of the key, so a newly discovered release is searched straight away.
    key = hashlib.sha1(f"q|{ra:.4f}|{dec:.4f}|{sorted(detectors)}|{sorted(collections)}".encode()).hexdigest()[:16]
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CACHE_DIR / f"query_{key}.json"


def query_exposures(ra: float, dec: float, detectors: list[int], collections: list[str] | None = None,
                    fresh: bool = False) -> list[Exposure]:
    """All spectral images whose footprint contains (ra, dec), oldest first.

    Uses TAP rather than SIA: TAP sees new data immediately after the weekly ingest.
    Results are cached on disk for CACHE_TTL_DAYS (SPHEREx ingests weekly), which also
    protects the demo from IRSA search outages.
    """
    collections = collections or current_collections()
    path = _cache_path(ra, dec, detectors, collections)
    if not fresh and path.exists() and time.time() - path.stat().st_mtime < config.CACHE_TTL_DAYS * 86400:
        return [Exposure(**{**d, "epoch": -1}) for d in json.loads(path.read_text())]
    exps = _query_exposures_live(ra, dec, detectors, collections)
    path.write_text(json.dumps([e.to_dict() for e in exps]))
    return exps


def _query_exposures_live(ra: float, dec: float, detectors: list[int], collections: list[str]) -> list[Exposure]:
    """SIA first (fast, ~1 day ingest lag), TAP as fallback; either one can be down."""
    errors = []
    for fn in (_query_sia, _query_tap):
        try:
            return fn(ra, dec, detectors, collections)
        except UpstreamError as e:
            errors.append(str(e))
    raise UpstreamError(" / ".join(errors))


# ---------------------------------------------------------------- data releases

_RELEASE_NAME_RE = re.compile(r"^(dr|qr)(\d+)$")
_releases_cache: tuple[float, list[str]] | None = None


def release_rank(name: str) -> int:
    """Newer is higher: any DR outranks any QR; within a kind, the higher number wins."""
    m = _RELEASE_NAME_RE.match(name or "")
    if not m:
        return 0
    return (1000 if m.group(1) == "dr" else 0) + int(m.group(2))


def pick_releases(names: list[str]) -> list[str]:
    """Newest data release plus newest quick release (QRs carry data not yet in a DR)."""
    out = []
    for kind in ("dr", "qr"):
        same = [n for n in names if n.startswith(kind) and _RELEASE_NAME_RE.match(n)]
        if same:
            out.append(max(same, key=release_rank))
    return out


def available_releases() -> list[str]:
    """Release names in the public S3 bucket (dr1, qr3, ...), newest first; listed once a day.
    A new release folder is picked up without any code change."""
    global _releases_cache
    if _releases_cache and time.time() - _releases_cache[0] < config.RELEASE_REFRESH_S:
        return _releases_cache[1]
    try:
        r = httpx.get(config.S3_BASE, params={"list-type": "2", "delimiter": "/"}, timeout=20)
        names = [p.strip("/") for p in re.findall(r"<Prefix>([^<]+)</Prefix>", r.text)]
        found = sorted((n for n in names if _RELEASE_NAME_RE.match(n)), key=release_rank, reverse=True)
    except httpx.HTTPError:
        found = []
    if not found:  # bucket unreachable: fall back to the configured defaults, don't cache the failure long
        return [c.removeprefix("spherex_") for c in config.DEFAULT_SIA_COLLECTIONS]
    _releases_cache = (time.time(), found)
    return found


def valid_release(name: str) -> bool:
    return name == "auto" or bool(_RELEASE_NAME_RE.match(name or ""))


def resolve_collections(release: str = "auto", deep: bool = False) -> list[str]:
    """SIA collections for a user choice: 'auto' = newest DR + newest QR (or SIA_COLLECTIONS if
    pinned), otherwise that one release. deep=True adds the matching Deep Field collections."""
    if release == "auto":
        if config.SIA_COLLECTIONS and not deep:
            return list(config.SIA_COLLECTIONS)
        names = pick_releases(available_releases())
    else:
        names = [release]
    cols = [f"spherex_{n}" for n in names]
    if deep:
        cols += [f"spherex_{n}_deep" for n in names]
    return cols


def current_collections() -> list[str]:
    """Default collections ('auto', wide survey only)."""
    return resolve_collections("auto", False)


def _query_sia(ra: float, dec: float, detectors: list[int], collections: list[str]) -> list[Exposure]:
    """One request per collection, so a release IRSA's SIA does not serve yet cannot break the rest."""
    wanted = {f"SPHEREx-D{d}" for d in detectors}
    out, errors = [], []
    for col in collections:
        params = [("COLLECTION", col), ("POS", f"circle {ra:.7f} {dec:.7f} 0.0003"), ("RESPONSEFORMAT", "votable")]
        r, last = None, ""
        for attempt in range(4):  # transient failures are retried with backoff
            try:
                r = httpx.get(config.IRSA_SIA, params=params, headers=config.IRSA_HEADERS,
                              timeout=httpx.Timeout(connect=15, read=config.TAP_DEADLINE_S, write=15, pool=15))
                if r.status_code < 500:
                    break
                last = f"HTTP {r.status_code}"
            except httpx.HTTPError as e:
                r, last = None, type(e).__name__
            time.sleep(2 * (attempt + 1))
        if r is None or r.status_code >= 500:
            # Never accept a partial answer: missing one release would silently drop epochs and
            # the incomplete result would be cached as if it were the whole sky history.
            raise UpstreamError(f"IRSA SIA unavailable for {col} ({last}). Try again shortly.")
        if r.status_code != 200:
            errors.append(f"{col}: HTTP {r.status_code}")  # e.g. a collection this release does not have
            continue
        t = Table.read(io.BytesIO(r.content), format="votable", use_names_over_ids=True)
        for row in t:
            band = str(row["energy_bandpassname"])
            if band not in wanted or str(row["dataproduct_subtype"]) != "science":
                continue
            out.append(_exposure(str(row["access_url"]).replace(config.IRSA_PUBLIC, ""),
                                 float(row["t_min"]), int(band[-1])))
    if not out and errors:
        raise UpstreamError(f"IRSA SIA unavailable ({'; '.join(errors)})")
    return dedupe(out)


def _exposure(uri: str, mjd: float, det: int) -> Exposure:
    m = _OBSID_RE.search(uri)
    rel = _RELEASE_RE.search(uri)
    return Exposure(uri=uri.lstrip("/"), mjd=mjd, detector=det,
                    obsid=m.group(1) if m else uri.rsplit("/", 1)[-1], release=rel.group(1) if rel else "")


def _query_tap(ra: float, dec: float, detectors: list[int], collections: list[str]) -> list[Exposure]:
    bands = ", ".join(f"'SPHEREx-D{d}'" for d in sorted(detectors))
    adql = f"""
SELECT a.uri, p.time_bounds_lower, p.energy_bandpassname
FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid
WHERE 1 = CONTAINS(POINT('ICRS', {ra:.7f}, {dec:.7f}), p.poly)
  AND p.energy_bandpassname IN ({bands})
ORDER BY p.time_bounds_lower
"""
    t = _tap_query(adql)
    # TAP sees every release; keep only the chosen ones (Deep Field files live under the same names).
    wanted = {c.removeprefix("spherex_").removesuffix("_deep") for c in collections}
    exps = [_exposure(str(row["uri"]), float(row["time_bounds_lower"]), int(str(row["energy_bandpassname"])[-1]))
            for row in t]
    return dedupe([e for e in exps if e.release in wanted])


def dedupe(exps: list[Exposure]) -> list[Exposure]:
    """Keep one image per (obsid, detector), preferring the newest release (see release_rank)."""
    best: dict[tuple, Exposure] = {}
    for e in exps:
        k = (e.obsid, e.detector)
        if k not in best or release_rank(e.release) > release_rank(best[k].release):
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
