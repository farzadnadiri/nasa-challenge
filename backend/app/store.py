"""Shared store for finished jobs: R2, reached through the Worker's internal /api/_store/ route.

Every backend deploy starts a fresh container with an empty disk. With the stacked images kept
here, the new container picks a finished view up in seconds instead of rebuilding it from the
SPHEREx archive (1-3 minutes per view). Objects are split into parts because the Worker limits
request bodies to 100 MB; a small manifest, written last, says how to put them back together.
"""
from __future__ import annotations

import hashlib
import json
import math

import httpx

from . import config

CHUNK_BYTES = 40_000_000

_client = httpx.Client(timeout=httpx.Timeout(connect=10, read=120, write=120, pool=10), headers=config.IRSA_HEADERS)


def enabled() -> bool:
    return bool(config.STORE_BASE and config.IRSA_PROXY_KEY)


def put(name: str, data: bytes) -> bool:
    """Best effort: False when the store is off or unreachable (the local copy still works)."""
    if not enabled():
        return False
    parts = max(1, math.ceil(len(data) / CHUNK_BYTES))
    try:
        for i in range(parts):
            _client.put(f"{config.STORE_BASE}{name}.{i}",
                        content=data[i * CHUNK_BYTES:(i + 1) * CHUNK_BYTES]).raise_for_status()
        meta = {"parts": parts, "size": len(data), "sha1": hashlib.sha1(data).hexdigest()}
        _client.put(f"{config.STORE_BASE}{name}.meta", content=json.dumps(meta).encode()).raise_for_status()
        return True
    except httpx.HTTPError:
        return False


def get(name: str) -> bytes | None:
    """The stored object, or None when it is missing, incomplete or the store is unreachable."""
    if not enabled():
        return None
    try:
        r = _client.get(f"{config.STORE_BASE}{name}.meta")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        meta = r.json()
        chunks = []
        for i in range(int(meta["parts"])):
            p = _client.get(f"{config.STORE_BASE}{name}.{i}")
            p.raise_for_status()
            chunks.append(p.content)
        data = b"".join(chunks)
        # parts from an overwrite in progress never pass for the stored object
        ok = len(data) == meta["size"] and hashlib.sha1(data).hexdigest() == meta["sha1"]
        return data if ok else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
