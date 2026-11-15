"""Runtime configuration. Everything that changes between SPHEREx data releases lives here."""
import os
from pathlib import Path

IRSA_PUBLIC = "https://irsa.ipac.caltech.edu/"
# On Cloudflare, IRSA blocks the container network, so IRSA_BASE points at the Worker's
# /api/_irsa/ proxy and every IRSA request carries the shared proxy key.
IRSA_BASE = os.environ.get("IRSA_BASE", IRSA_PUBLIC)
IRSA_PROXY_KEY = os.environ.get("IRSA_PROXY_KEY", "")
IRSA_HEADERS = {"X-Proxy-Key": IRSA_PROXY_KEY} if IRSA_PROXY_KEY else {}
# Uploaded datasets are read through the Worker's internal /api/_upload/ route (same key).
UPLOAD_BASE = os.environ.get("UPLOAD_BASE", "http://localhost:8787/api/_upload/")
IRSA_TAP = IRSA_BASE + "TAP/sync"
IRSA_SIA = IRSA_BASE + "SIA"
# Releases are discovered automatically (newest DR + newest QR, from the S3 bucket's folders).
# Set SIA_COLLECTIONS (comma separated) to pin them; the defaults are used if discovery fails.
SIA_COLLECTIONS = [c for c in os.environ.get("SIA_COLLECTIONS", "").split(",") if c]
DEFAULT_SIA_COLLECTIONS = ("spherex_dr1", "spherex_qr3")
RELEASE_REFRESH_S = 24 * 3600
REFRESH_MIN_S = float(os.environ.get("REFRESH_MIN_S", "3600"))  # "check for new data" at most hourly per view
# Public SPHEREx bucket; object keys are the IRSA paths without the ibe/data/spherex/ prefix.
S3_BASE = os.environ.get("S3_BASE", "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/")
GAIA_TAP = os.environ.get("GAIA_TAP", "https://gea.esac.esa.int/tap-server/tap/sync")
SIMBAD_TAP = os.environ.get("SIMBAD_TAP", "https://simbad.cfa.harvard.edu/simbad/sim-tap/sync")  # Harvard mirror
SESAME = os.environ.get("SESAME", "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV")

# Bump when processing changes, so stale cached results are not served.
PIPELINE_VERSION = "3"
# Baked into the image and sent as X-Backend-Gen; the Worker caches a response only when this matches
# its CONTAINER_GEN. An old image still running during a rollout reports its own, older number, so
# its answers are never cached under the new generation. Bump together with CONTAINER_GEN in
# worker/wrangler.jsonc (tests/test_core.py checks they match).
BACKEND_GEN = "13"

CACHE_DIR = Path(os.environ.get("CACHE_DIR", Path(__file__).resolve().parents[1] / ".cache"))
CACHE_TTL_DAYS = float(os.environ.get("CACHE_TTL_DAYS", "7"))  # SPHEREx ingests weekly

TAP_DEADLINE_S = float(os.environ.get("TAP_DEADLINE_S", "240"))  # give up on a hung archive search
FETCH_WORKERS = int(os.environ.get("FETCH_WORKERS", "24"))
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "3"))
ABORT_AFTER_FAILURES = 10  # first N cutouts all failed -> stop the job
MAX_EXPOSURES = int(os.environ.get("MAX_EXPOSURES", "600"))

PIXEL_SCALE_ARCSEC = 6.15  # native SPHEREx pixel
MIN_FOV_DEG = 0.02
MAX_FOV_DEG = 0.25
# Jobs are processed at one of these sizes and every request is cropped from the job's grid,
# so changing the field of view never starts a new download (9' covers the usual views).
JOB_FOV_TIERS_DEG = (0.15, 0.25)

# Epochs: SPHEREx revisits a position every ~6 months. Exposures closer together than
# EPOCH_GAP_DAYS belong to the same visit; long visits (deep fields near the ecliptic
# poles) are split into windows of at most EPOCH_MAX_SPAN_DAYS.
EPOCH_GAP_DAYS = 20.0
EPOCH_MAX_SPAN_DAYS = 30.0
CHANNELS_PER_DETECTOR = 17  # SPHEREx: 6 detectors x 17 channels = 102 bands
MIN_EPOCH_EXPOSURES = 5  # fewer than this (or <20% of the median epoch) is marked shallow

# FLAGS bits that make a pixel unusable. Names are looked up in each file's FLAGS header,
# so bit numbering changes between pipeline versions are handled automatically.
BAD_FLAG_NAMES = {
    "TRANSIENT", "SUR_ERROR", "NONFUNC", "MISSING_DATA", "HOT", "COLD",
    "PERSIST", "OUTLIER", "SNOWBALL",
}
# Light from something else at the target (not masked; reported, and excluded from photometry).
CONTAMINATION_FLAG_NAMES = {"GHOST", "GHOST_FPA", "GHOST_EXT", "STREAK", "HALO", "CROSSTALK"}
# The target itself is too bright: these always appear together on saturated stars
# (R Dra, RW Aur, Barnard's Star), so they mean "saturated", not "contaminated".
SATURATION_FLAG_NAMES = {"OVERFLOW", "SUR_ERROR", "BLOOM", "NONLINEAR"}

# Approximate wavelength coverage per detector (micron), from the SPHEREx docs.
DETECTOR_RANGES = {
    1: (0.75, 1.09), 2: (1.10, 1.62), 3: (1.63, 2.41),
    4: (2.42, 3.82), 5: (3.83, 4.41), 6: (4.42, 5.00),
}
