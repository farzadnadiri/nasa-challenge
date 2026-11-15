import math

import pytest

from app.cutouts import make_grid
from app.irsa import Exposure, assign_epochs, dedupe
from app.resolve import parse_coordinates, propagate


def _exp(mjd, det=6, obsid=None, release="dr1"):
    return Exposure(uri="x", mjd=mjd, detector=det, obsid=obsid or f"o{mjd}", release=release)


def test_epochs_split_on_gaps():
    exps = [_exp(m) for m in (100, 101, 105, 290, 292, 470)]
    eps = assign_epochs(exps)
    assert [e["n_exposures"] for e in eps] == [3, 2, 1]
    assert [e.epoch for e in exps] == [0, 0, 0, 1, 1, 2]


def test_long_visits_split_into_windows():
    exps = [_exp(100 + 5 * i) for i in range(20)]  # continuous 95-day coverage
    eps = assign_epochs(exps)
    assert len(eps) >= 3
    assert all(e["mjd_end"] - e["mjd_start"] <= 30 for e in eps)


def test_dedupe_prefers_newest_release():
    out = dedupe([_exp(1, obsid="A", release="qr3"), _exp(1, obsid="A", release="dr1")])
    assert len(out) == 1 and out[0].release == "dr1"


@pytest.mark.parametrize("q,ra,dec", [
    ("133.795 -7.245", 133.795, -7.245),
    ("08h55m10.831s -07d14m42.53s", 133.795129, -7.245147),
    ("08:55:10.831 -07:14:42.53", 133.795129, -7.245147),
    ("08 55 10.831 -07 14 42.53", 133.795129, -7.245147),
])
def test_parse_coordinates(q, ra, dec):
    c = parse_coordinates(q)
    assert c is not None
    assert c.ra.deg == pytest.approx(ra, abs=1e-5) and c.dec.deg == pytest.approx(dec, abs=1e-5)


def test_names_are_not_coordinates():
    assert parse_coordinates("Barnard's Star") is None
    assert parse_coordinates("R Dra") is None


def test_propagate_barnards_star():
    ra, dec = propagate(269.45208, 4.69336, -801.551, 10362.394, 26.0)
    shift = math.hypot((ra - 269.45208) * math.cos(math.radians(dec)), dec - 4.69336) * 3600
    assert shift == pytest.approx(10.393 * 26, rel=1e-3)


def test_grid_is_odd_and_centred():
    w, n = make_grid(10.0, 20.0, 0.1)
    assert n % 2 == 1
    x, y = w.world_to_pixel_values(10.0, 20.0)
    assert x == pytest.approx((n - 1) / 2) and y == pytest.approx((n - 1) / 2)


@pytest.mark.parametrize("fov", [0.02, 0.08, 0.1, 0.12, 0.15])
def test_crop_stays_centred_on_target(fov):
    from types import SimpleNamespace
    from app.main import _crop
    job_grid, n_job = make_grid(10.0, 20.0, 0.15)
    n, off = _crop(SimpleNamespace(n=n_job, ra=10.0, dec=20.0, pixel_scale=6.15), fov)
    assert n % 2 == 1 and off + (n - 1) / 2 == (n_job - 1) / 2


def test_release_rank_and_pick():
    from app.irsa import pick_releases, release_rank
    assert release_rank("dr2") > release_rank("dr1") > release_rank("qr9") > release_rank("qr3") > release_rank("x")
    assert pick_releases(["dr1", "qr2", "qr3", "level2", "dr2"]) == ["dr2", "qr3"]
    assert pick_releases(["qr2", "qr3"]) == ["qr3"]


def test_dedupe_prefers_any_newer_release():
    out = dedupe([_exp(1, obsid="A", release="dr1"), _exp(1, obsid="A", release="dr2"), _exp(1, obsid="A", release="qr4")])
    assert out[0].release == "dr2"


def test_wavelength_frames_cover_102_bands():
    import numpy as np
    from app import config, jobs
    job = jobs.Job("k", 0.0, 0.0, 0.1, [5, 6], status="done", n=3, epochs=[{"index": 0}])
    for det, wl in [(5, 3.84), (5, 4.40), (6, 4.43), (6, 5.00)]:
        job.metas.append({"detector": det, "wavelength_um": wl, "epoch": 0, "target_flags": []})
        job.images.append(np.ones((3, 3), np.float32))
        job.variances.append(np.ones((3, 3), np.float32))
    frames = jobs.wavelength_frames(job)
    assert len(frames) == 2 * config.CHANNELS_PER_DETECTOR
    assert [f["wavelength_um"] for f in frames] == sorted(f["wavelength_um"] for f in frames)
    assert sum(f["n_used"] for f in frames) == 4  # every exposure lands in exactly one channel
    assert frames[-1]["n_used"] == 1  # 5.00 um, the top edge, is included


def test_resolve_collections(monkeypatch):
    from app import config, irsa
    monkeypatch.setattr(config, "SIA_COLLECTIONS", [])
    monkeypatch.setattr(irsa, "available_releases", lambda: ["dr2", "dr1", "qr4", "qr3"])
    assert irsa.resolve_collections("auto") == ["spherex_dr2", "spherex_qr4"]
    assert irsa.resolve_collections("dr1") == ["spherex_dr1"]
    assert irsa.resolve_collections("dr1", deep=True) == ["spherex_dr1", "spherex_dr1_deep"]
    assert irsa.valid_release("auto") and irsa.valid_release("qr3") and not irsa.valid_release("x; drop")


def test_jobs_are_keyed_by_release():
    from app import jobs
    a = jobs.job_key(1.0, 2.0, 0.15, [5, 6], ["spherex_dr1"])
    b = jobs.job_key(1.0, 2.0, 0.15, [5, 6], ["spherex_qr3"])
    assert a != b and a == jobs.job_key(1.0, 2.0, 0.15, [6, 5], ["spherex_dr1"])


def test_refresh_restarts_a_finished_view_once_cooldown_passed(monkeypatch):
    from app import config, jobs
    started = []
    monkeypatch.setattr(jobs, "_run", lambda job: started.append(job))          # no archive calls
    monkeypatch.setattr(jobs.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    key = jobs.job_key(1.0, 2.0, 0.15, [6], ["spherex_dr1"])
    old = jobs.Job(key, 1.0, 2.0, 0.15, [6], ["spherex_dr1"], status="done")
    monkeypatch.setitem(jobs._jobs, key, old)

    monkeypatch.setattr(config, "REFRESH_MIN_S", 3600.0)
    assert jobs.get_or_start(1.0, 2.0, 0.15, [6], ["spherex_dr1"], refresh=True) is old  # too soon
    assert old.refresh_wait > 3500 and not started

    monkeypatch.setattr(config, "REFRESH_MIN_S", 0.0)
    new = jobs.get_or_start(1.0, 2.0, 0.15, [6], ["spherex_dr1"], refresh=True)
    assert new is not old and new.fresh and started == [new]                       # fresh archive search


def _upload_fits(with_wcs=True, with_time=True, extra=None):
    """A small FITS file shaped like a user upload."""
    import io
    import numpy as np
    from astropy.io import fits
    hdr = fits.Header()
    if with_wcs:
        hdr.update({"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": 150.0, "CRVAL2": 2.0,
                    "CRPIX1": 50.5, "CRPIX2": 50.5, "CDELT1": -2 / 3600, "CDELT2": 2 / 3600})
    if with_time:
        hdr["DATE-OBS"] = "2025-11-01T03:04:05"
    hdr["WAVELEN"] = 0.65
    hdr["BUNIT"] = "ADU"
    hdus = [fits.PrimaryHDU(np.random.default_rng(1).normal(100, 5, (100, 100)).astype(np.float32), hdr)]
    hdus += extra or []
    buf = io.BytesIO()
    fits.HDUList(hdus).writeto(buf)
    return buf.getvalue()


def test_upload_standard_accepts_a_valid_file():
    import numpy as np
    from astropy.io import fits
    from app.uploads import read_upload
    sig = fits.ImageHDU(np.full((100, 100), 2.0, np.float32), name="ERR")
    f = read_upload(_upload_fits(extra=[sig]), "a.fits")
    assert abs(f["mjd"] - 60980.128) < 0.01 and f["wavelength_um"] == 0.65 and f["bunit"] == "ADU"
    assert abs(f["pixel_scale_arcsec"] - 2.0) < 1e-6 and float(f["variance"][0, 0]) == 4.0  # ERR -> variance
    assert abs(f["center"][0] - 150.0) < 1e-3 and abs(f["center"][1] - 2.0) < 1e-3


@pytest.mark.parametrize("raw,reason", [
    (b"not a fits file at all", "not a FITS file"),
    ("no-wcs", "no celestial WCS"),
    ("no-time", "no observation time"),
])
def test_upload_standard_rejects_with_reason(raw, reason):
    from app.uploads import UploadError, read_upload
    if raw == "no-wcs":
        raw = _upload_fits(with_wcs=False)
    elif raw == "no-time":
        raw = _upload_fits(with_time=False)
    with pytest.raises(UploadError, match=reason):
        read_upload(raw, "x.fits")


def test_backend_generation_matches_worker_config():
    import pathlib
    import re
    from app import config
    wrangler = (pathlib.Path(__file__).resolve().parents[2] / "worker" / "wrangler.jsonc").read_text(encoding="utf-8")
    gen = re.search(r'"CONTAINER_GEN":\s*"(\d+)"', wrangler).group(1)
    assert config.BACKEND_GEN == gen, "bump BACKEND_GEN in backend/app/config.py together with CONTAINER_GEN"


def _synthetic_mover_job(n=81, scale=6.15, years=(2025.3, 2025.8, 2026.3, 2026.8, 2027.3), v_px_yr=1.0,
                         mover_flux=300.0, skip_mover=(), extra_stars=None, n_stars=15):
    import numpy as np
    from app import jobs
    from app.cutouts import make_grid
    fov = n * scale / 3600
    job = jobs.Job("mv", 10.0, 20.0, fov, [6], ["spherex_dr1"], status="done", pixel_scale=scale)
    rng = np.random.default_rng(5)
    stars = [(rng.uniform(8, n - 8), rng.uniform(8, n - 8), rng.uniform(80, 400)) for _ in range(n_stars)]
    yy, xx = np.mgrid[0:n, 0:n]
    for i, yr in enumerate(years):
        img = rng.normal(0, 1, (n, n))
        for x, y, f in stars:
            img += f * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 0.8 ** 2))
        if extra_stars and i in extra_stars:  # sources seen in this epoch only
            for x, y, f in extra_stars[i]:
                img += f * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 0.8 ** 2))
        mx = 30 + v_px_yr * (yr - years[0])
        if i not in skip_mover:
            img += mover_flux * np.exp(-((xx - mx) ** 2 + (yy - 50) ** 2) / (2 * 0.8 ** 2))
        job.images.append(img.astype(np.float32))
        job.variances.append(np.ones((n, n), np.float32))
        job.metas.append({"epoch": i, "psf_fwhm_arcsec": 1.9 * scale})
        job.epochs.append({"index": i, "decimal_year": yr})
    frames = [{"epoch": e, "n_used": 1, "shallow": False} for e in job.epochs]
    grid, _ = make_grid(job.ra, job.dec, job.fov, scale)
    return job, frames, grid


FAR_STAR = {"source_id": "9", "ra": 10.0, "dec": 21.0, "pmra": 0.0, "pmdec": 0.0, "phot_g_mean_mag": 9.0}


def test_moving_source_without_gaia_is_a_candidate(monkeypatch):
    from app import movers
    job, frames, grid = _synthetic_mover_job()
    monkeypatch.setattr(movers.gaia, "cone", lambda *a, **k: {"sources": [FAR_STAR]})
    res = movers.find_movers(job, frames)
    assert len(res["movers"]) == 1, res["movers"]          # the still stars are not flagged
    m = res["movers"][0]
    assert m["status"] == "candidate" and m["n_epochs"] == 5
    assert abs(m["pm_arcsec_yr"] - 6.15) < 0.6             # 1 px/yr at 6.15"/px
    assert m["pmra_mas_yr"] < 0                            # +x on the grid is west


def test_moving_source_matching_gaia_is_known(monkeypatch):
    from app import movers
    job, frames, grid = _synthetic_mover_job()
    # A Gaia star that sits on the track and moves the same way (pm in mas/yr, position at 2016.0).
    ra0, dec0 = grid.wcs_pix2world([[30 + 1.0 * (2016.0 - 2025.3), 50]], 0)[0]
    star = {"source_id": "1", "ra": float(ra0), "dec": float(dec0), "pmra": -6150.0, "pmdec": 0.0,
            "phot_g_mean_mag": 12.0, "name": "Test star"}
    monkeypatch.setattr(movers.gaia, "cone", lambda *a, **k: {"sources": [star]})
    m = movers.find_movers(job, frames)["movers"][0]
    assert m["status"] == "known" and m["gaia"]["name"] == "Test star"


def test_mover_missing_from_the_richest_epoch_is_found(monkeypatch):
    # Epoch 2 has the most detections (extra one-off sources) but not the mover: tracks must
    # still start from other epochs.
    from app import movers
    extra = {2: [(12.0 + 9 * k, 70.0, 250.0) for k in range(6)]}
    job, frames, grid = _synthetic_mover_job(skip_mover=(2,), extra_stars=extra)
    monkeypatch.setattr(movers.gaia, "cone", lambda *a, **k: {"sources": [FAR_STAR]})
    res = movers.find_movers(job, frames)
    assert len(res["movers"]) == 1, res["movers"]
    m = res["movers"][0]
    assert m["status"] == "candidate" and m["n_epochs"] == 4
    assert abs(m["pm_arcsec_yr"] - 6.15) < 0.6


def test_star_cut_by_the_coverage_edge_is_not_measured():
    import numpy as np
    from app import movers
    n = 61
    yy, xx = np.mgrid[0:n, 0:n]
    img = np.random.default_rng(1).normal(0, 1, (n, n)) + 400 * np.exp(-((xx - 30) ** 2 + (yy - 30) ** 2) / (2 * 0.8 ** 2))
    img[:, 31:] = np.nan  # this epoch did not cover the right side, through the star
    d = movers.detect(img, np.ones((n, n)), 2.5)
    assert not np.any(np.hypot(d["x"] - 30, d["y"] - 30) < 3), list(zip(d["x"], d["y"]))
    assert movers.measure_at(d, 30.0, 30.0) is None
    # a small masked core inside the field is a saturated star, still measured and flagged
    img2 = np.random.default_rng(2).normal(0, 1, (n, n)) + 400 * np.exp(-((xx - 30) ** 2 + (yy - 30) ** 2) / (2 * 1.2 ** 2))
    img2[29:32, 29:32] = np.nan
    d2 = movers.detect(img2, np.ones((n, n)), 2.5)
    k = np.argmin(np.hypot(d2["x"] - 30, d2["y"] - 30))
    assert abs(d2["x"][k] - 30) < 0.3 and abs(d2["y"][k] - 30) < 0.3 and d2["sat"][k]


def test_motion_on_a_still_gaia_star_is_doubtful_not_a_candidate(monkeypatch):
    from app import movers
    # slow motion (0.3 px/yr, 2 yr) that never leaves a Gaia star with no proper motion
    job, frames, grid = _synthetic_mover_job(v_px_yr=0.3)
    ra0, dec0 = grid.wcs_pix2world([[30 + 0.3, 50]], 0)[0]
    star = {"source_id": "7", "ra": float(ra0), "dec": float(dec0), "pmra": 0.0, "pmdec": 0.0,
            "phot_g_mean_mag": 14.0, "name": "Still star"}
    monkeypatch.setattr(movers.gaia, "cone", lambda *a, **k: {"sources": [star]})
    ms = movers.find_movers(job, frames)["movers"]
    assert len(ms) == 1 and ms[0]["status"] == "doubtful" and ms[0]["reason"] == "gaia_motion" and ms[0]["gaia"]["name"] == "Still star"


def test_gaia_unavailable_marks_movers_unchecked(monkeypatch):
    from app import movers

    def down(*a, **k):
        raise movers.UpstreamError("down")
    job, frames, grid = _synthetic_mover_job()
    monkeypatch.setattr(movers.gaia, "cone", down)
    res = movers.find_movers(job, frames)
    assert not res["gaia_checked"] and [m["status"] for m in res["movers"]] == ["unchecked"]


def test_no_detection_is_shared_between_tracks():
    import numpy as np
    from app import movers
    # two still stars 2 px apart and one epoch where only one of them is seen
    def det(xs):
        xs = np.array(xs, float)
        return {"x": xs, "y": np.full(len(xs), 20.0), "err": np.full(len(xs), 0.1), "stat": np.full(len(xs), 0.05),
                "flux": np.full(len(xs), 100.0), "snr": np.full(len(xs), 50.0), "sat": np.zeros(len(xs), bool)}
    dets = [det([20, 22]), det([20, 22]), det([21]), det([20, 22])]
    tracks = movers.link(dets, [2025.0, 2025.5, 2026.0, 2026.5], 6.15)
    seen = [(e, j) for t in tracks for e, j in t.items()]
    assert len(seen) == len(set(seen)) and len(tracks) == 2


def test_very_bright_mover_with_small_systematics_is_kept(monkeypatch):
    # A bright star whose centroid wobbles by ~0.1 px (parallax, saturation) must not fail the
    # straight-line test just because the field's calibrated floor is tiny.
    import numpy as np
    from app import movers
    job, frames, grid = _synthetic_mover_job(mover_flux=20000.0, years=(2025.3, 2025.8, 2026.3))
    shifts = [0.1, -0.1, 0.1]
    for i, sh in enumerate(shifts):
        n = job.images[i].shape[0]
        yy, xx = np.mgrid[0:n, 0:n]
        mx = 30 + 1.0 * (job.epochs[i]["decimal_year"] - 2025.3)
        job.images[i] -= (20000 * np.exp(-((xx - mx) ** 2 + (yy - 50) ** 2) / (2 * 0.8 ** 2))).astype(np.float32)
        job.images[i] += (20000 * np.exp(-((xx - mx) ** 2 + (yy - 50 - sh) ** 2) / (2 * 0.8 ** 2))).astype(np.float32)
    monkeypatch.setattr(movers.gaia, "cone", lambda *a, **k: {"sources": [FAR_STAR]})
    ms = movers.find_movers(job, frames)["movers"]
    assert len(ms) == 1 and abs(ms[0]["pm_arcsec_yr"] - 6.15) < 0.6, ms


def test_empty_gaia_answer_counts_as_unchecked(monkeypatch):
    from app import movers
    job, frames, grid = _synthetic_mover_job()
    monkeypatch.setattr(movers.gaia, "cone", lambda *a, **k: {"sources": []})
    res = movers.find_movers(job, frames)
    assert not res["gaia_checked"] and [m["status"] for m in res["movers"]] == ["unchecked"]


def test_motion_resting_on_one_point_is_rejected(monkeypatch):
    # A still source whose first epoch is 1 px off (a bad centroid) must not become a mover.
    import numpy as np
    from app import movers
    job, frames, grid = _synthetic_mover_job(v_px_yr=0.0)
    n = job.images[0].shape[0]
    yy, xx = np.mgrid[0:n, 0:n]
    job.images[0] -= (300 * np.exp(-((xx - 30) ** 2 + (yy - 50) ** 2) / (2 * 0.8 ** 2))).astype(np.float32)
    job.images[0] += (300 * np.exp(-((xx - 31) ** 2 + (yy - 50.6) ** 2) / (2 * 0.8 ** 2))).astype(np.float32)
    monkeypatch.setattr(movers.gaia, "cone", lambda *a, **k: {"sources": [FAR_STAR]})
    assert movers.find_movers(job, frames)["movers"] == []


def test_saturated_mover_without_gaia_is_doubtful(monkeypatch):
    import numpy as np
    from app import movers
    job, frames, grid = _synthetic_mover_job()
    for i, ep in enumerate(job.epochs):  # mask the mover's core in every epoch, as saturation does
        mx = int(round(30 + 1.0 * (ep["decimal_year"] - 2025.3)))
        job.images[i][50, mx] = np.nan
    monkeypatch.setattr(movers.gaia, "cone", lambda *a, **k: {"sources": [FAR_STAR]})
    ms = movers.find_movers(job, frames)["movers"]
    assert len(ms) == 1 and ms[0]["status"] == "doubtful" and ms[0]["reason"] == "saturated"


def test_result_versions_match_worker_config():
    import json
    import pathlib
    import re
    from app import config
    wrangler = (pathlib.Path(__file__).resolve().parents[2] / "worker" / "wrangler.jsonc").read_text(encoding="utf-8")
    worker = json.loads(re.search(r'"RESULT_VERSIONS":\s*(\{[^}]*\})', wrangler).group(1))
    assert worker == config.RESULT_VERSIONS, "keep RESULT_VERSIONS in backend/app/config.py and worker/wrangler.jsonc equal"


def test_store_splits_and_reassembles(monkeypatch):
    import httpx
    from app import config, store
    objects = {}

    def handler(req: httpx.Request):
        name = req.url.path.split("/api/_store/")[1]
        if req.method == "PUT":
            objects[name] = req.content
            return httpx.Response(200)
        return httpx.Response(200, content=objects[name]) if name in objects else httpx.Response(404)

    monkeypatch.setattr(config, "STORE_BASE", "https://x/api/_store/")
    monkeypatch.setattr(config, "IRSA_PROXY_KEY", "k")
    monkeypatch.setattr(store, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(store, "CHUNK_BYTES", 1000)
    data = bytes(range(256)) * 10  # 2560 bytes -> 3 parts
    assert store.put("jobs/abc.npz", data)
    assert sorted(objects) == ["jobs/abc.npz.0", "jobs/abc.npz.1", "jobs/abc.npz.2", "jobs/abc.npz.meta"]
    assert store.get("jobs/abc.npz") == data
    assert store.get("jobs/missing.json") is None
    objects["jobs/abc.npz.1"] = b"x" * 1000  # an overwrite caught half way is never returned
    assert store.get("jobs/abc.npz") is None


def test_finished_job_survives_a_new_container(monkeypatch, tmp_path):
    # A job saved by one container is restored by the next one (empty disk) from the shared store.
    import time
    import numpy as np
    from app import config, jobs, store
    saved = {}
    monkeypatch.setattr(store, "put", lambda name, data: saved.__setitem__(name, data) or True)
    monkeypatch.setattr(store, "get", lambda name: saved.get(name))
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "a")
    job = jobs.Job("k1", 10.0, 20.0, 0.1, [6], ["spherex_dr1"], status="done", pixel_scale=6.15, n=2, total=2,
                   epochs=[{"index": 0, "decimal_year": 2025.5}], metas=[{"epoch": 0}, {"epoch": 0}])
    job.images = [np.ones((5, 5), np.float32)] * 2
    job.variances = [np.ones((5, 5), np.float32)] * 2
    job.contams = [None, None]
    jobs._save_cached(job)
    for _ in range(100):  # the upload runs in the background
        if "jobs/k1.json" in saved:
            break
        time.sleep(0.02)
    assert set(saved) == {"jobs/k1.npz", "jobs/k1.json"}
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "b")  # the new container's empty disk
    back = jobs._restore("k1")
    assert back and back.status == "done" and len(back.images) == 2 and back.pixel_scale == 6.15


def test_photometry_contamination_is_judged_at_the_clicked_spot():
    import numpy as np
    from app import jobs
    job = jobs.Job("ph", 10.0, 20.0, 0.1, [6], ["spherex_dr1"], status="done", pixel_scale=6.15)
    n = 21
    for i in range(2):
        job.images.append(np.ones((n, n), np.float32))
        job.variances.append(np.ones((n, n), np.float32))
        job.metas.append({"mjd": 60800.0 + i, "epoch": 0, "detector": 6, "wavelength_um": 4.5,
                          "target_flags": ["HALO"] if i == 0 else []})  # flagged at the field centre only
        mask = np.zeros((n, n), np.uint8)
        if i == 1:
            mask[3:6, 3:6] = 1  # a ghost on the clicked source
        job.contams.append(mask)
    pts, _ = jobs.photometry(job, 4.0, 4.0)
    assert [p["contaminated"] for p in sorted(pts, key=lambda p: p["mjd"])] == [False, True]
