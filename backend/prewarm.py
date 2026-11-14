"""Fill the result cache for the challenge targets so the demo is instant.

    python prewarm.py                       # against a local server on :8000
    python prewarm.py https://<worker-url>  # against the deployed Worker (fills R2 too)
"""
import sys
import time

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
TARGETS = [  # (query, fov deg, detectors) matching the frontend presets
    ("Barnard's Star", 0.1, "5,6"),
    ("eps Ind B", 0.1, "5,6"),
    ("WISE J085510.83-071442.5", 0.12, "5,6"),
    ("RW Aur", 0.08, "5,6"),
    ("R Dra", 0.08, "5,6"),
]


def warm(client: httpx.Client, q: str, fov: float, dets: str):
    r = client.get(f"{BASE}/api/resolve", params={"q": q}).json()
    params = {"ra": f"{r['ra']:.6f}", "dec": f"{r['dec']:.6f}", "fov": f"{fov:.3f}", "dets": dets}
    t0 = time.time()
    while True:
        try:
            d = client.get(f"{BASE}/api/frames", params={**params, "mode": "epoch"}).json()
        except httpx.TransportError as e:  # transient; keep polling
            print(f"\n  retrying after {type(e).__name__}")
            time.sleep(5)
            continue
        p = d["progress"]
        print(f"\r{q:32s} {d['status']:9s} {p['done']}/{p['total']} failed={p['failed']} "
              f"epochs={len(d['epochs'])} {time.time() - t0:5.0f}s", end="", flush=True)
        if d["status"] in ("done", "error"):
            print(f"  {d.get('error') or ''}")
            break
        time.sleep(5)
    if d["status"] == "done":  # also cache the exposure-mode response in R2 when going through the Worker
        client.get(f"{BASE}/api/frames", params={**params, "mode": "exposure"})


if __name__ == "__main__":
    # Not run in parallel: the backend processes at most two jobs at once anyway.
    with httpx.Client(timeout=300) as c:
        for t in TARGETS:
            warm(c, *t)
