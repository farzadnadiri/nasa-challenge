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
