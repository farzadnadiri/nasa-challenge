"""Read just the needed pixels of a SPHEREx spectral image straight from the public S3 bucket.

IRSA's cutout service returns ~4 MB per request (mostly the PSF table we don't use) and takes
seconds to build each cutout. Here we walk the FITS headers with HTTP range requests and fetch
only: the IMAGE and FLAGS rows covering the field, plus the small WCS-WAVE table.
"""
from __future__ import annotations

import numpy as np
import httpx
from astropy.io import fits
from astropy.wcs import WCS

from . import config

BLOCK = 2880
HEADER_CHUNK = 32 * 1024  # SPHEREx headers are < 30 KB; one request usually covers one


class RangeFile:
    def __init__(self, client: httpx.Client, url: str):
        self.client, self.url = client, url

    def read(self, start: int, length: int) -> bytes:
        r = self.client.get(self.url, headers={"Range": f"bytes={start}-{start + length - 1}"})
        if r.status_code not in (200, 206):
            raise RuntimeError(f"S3 HTTP {r.status_code}")
        return r.content


def _parse_header(f: RangeFile, offset: int, buf: bytes | None = None) -> tuple[fits.Header, int, bytes]:
    """Parse the header starting at offset. Returns (header, data_offset, leftover bytes after header)."""
    data = buf or b""
    while True:
        end = -1
        for i in range(0, len(data) - 79, 80):
            if data[i:i + 8] == b"END     ":
                end = i
                break
        if end >= 0:
            break
        data += f.read(offset + len(data), HEADER_CHUNK)
    hlen = ((end + 80 + BLOCK - 1) // BLOCK) * BLOCK
    return fits.Header.fromstring(data[:end + 80].decode("ascii")), offset + hlen, data[hlen:]


def _data_size(h: fits.Header) -> int:
    n = abs(h["BITPIX"]) // 8 * h.get("GCOUNT", 1)
    size = 0
    if h.get("NAXIS", 0):
        size = 1
        for i in range(1, h["NAXIS"] + 1):
            size *= h[f"NAXIS{i}"]
        size = (size + h.get("PCOUNT", 0)) * n
    return ((size + BLOCK - 1) // BLOCK) * BLOCK


def _compressed_rows(f: RangeFile, hdr: fits.Header, data_off: int, y0: int, y1: int) -> np.ndarray:
    """Rows y0..y1 of a tile-compressed image whose tiles are single rows (FLAGS: RICE_1, ZTILE2=1)."""
    if hdr.get("ZCMPTYPE") != "RICE_1" or hdr.get("ZTILE2", 1) != 1:
        raise RuntimeError(f"unsupported FLAGS compression {hdr.get('ZCMPTYPE')}/{hdr.get('ZTILE2')}")
    from astropy.io.fits.hdu.compressed._codecs import Rice1

    width = hdr["ZNAXIS1"]
    params = {str(hdr.get(f"ZNAME{i}")).upper(): hdr.get(f"ZVAL{i}") for i in range(1, 5) if f"ZNAME{i}" in hdr}
    blocksize, bytepix = int(params.get("BLOCKSIZE", 32)), int(params.get("BYTEPIX", 4))
    row_len = hdr["NAXIS1"]  # bytes per table row (one 8-byte heap descriptor)
    heap_start = data_off + hdr.get("THEAP", row_len * hdr["NAXIS2"])
    desc = np.frombuffer(f.read(data_off + y0 * row_len, (y1 - y0) * row_len), dtype=">i4").reshape(-1, 2)
    lo, hi = int(desc[:, 1].min()), int((desc[:, 1] + desc[:, 0]).max())
    heap = f.read(heap_start + lo, hi - lo)
    codec = Rice1(blocksize=blocksize, bytepix=bytepix, tilesize=width)
    out = np.empty((y1 - y0, width), dtype=np.int64)
    for i, (count, offset) in enumerate(desc):
        chunk = np.frombuffer(heap, dtype=np.uint8, count=int(count), offset=int(offset) - lo)
        out[i] = np.asarray(codec.decode(chunk)).view(f"i{bytepix}" if bytepix > 1 else "u1")[:width]
    return out


def read_section(client: httpx.Client, key: str, ra: float, dec: float, size_deg: float) -> dict:
    """Returns {'header','image','flags','flags_header','x0','y0','wcs_wave_hdul'} for a box around (ra, dec)."""
    f = RangeFile(client, config.S3_BASE + key)
    prim, off, rest = _parse_header(f, 0)
    hdus: dict[str, tuple[fits.Header, int]] = {}
    while True:
        h, data_off, rest = _parse_header(f, off, rest)
        name = str(h.get("EXTNAME", "")).strip()
        hdus[name] = (h, data_off)
        size = _data_size(h)
        off = data_off + size
        # Leftover bytes only help if the data block was fully inside them.
        rest = rest[size:] if len(rest) > size else b""
        if name == "WCS-WAVE":
            wave_hdr, wave_off = h, data_off
            break

    img_hdr, img_off = hdus["IMAGE"]
    ny, nx = img_hdr["NAXIS2"], img_hdr["NAXIS1"]
    wcs = WCS(img_hdr)
    x, y = (float(v) for v in wcs.world_to_pixel_values(ra, dec))
    half = size_deg / 2 / (config.PIXEL_SCALE_ARCSEC / 3600) + 2
    x0, x1 = max(0, int(x - half)), min(nx, int(x + half) + 1)
    y0, y1 = max(0, int(y - half)), min(ny, int(y + half) + 1)
    if x0 >= x1 or y0 >= y1:
        raise RuntimeError("position not on this image")

    def rows(hdr: fits.Header, data_off: int, dtype: str) -> np.ndarray:
        row_bytes = nx * abs(hdr["BITPIX"]) // 8
        raw = f.read(data_off + y0 * row_bytes, (y1 - y0) * row_bytes)
        return np.frombuffer(raw, dtype=dtype).reshape(y1 - y0, nx)[:, x0:x1]

    flags_hdr, flags_off = hdus["FLAGS"]
    image = rows(img_hdr, img_off, ">f4").astype(np.float32)
    flags = _compressed_rows(f, flags_hdr, flags_off, y0, y1)[:, x0:x1]
    wave_bytes = f.read(wave_off, _data_size(wave_hdr))
    wave_hdu = fits.BinTableHDU.fromstring(wave_hdr.tostring().encode("ascii") + wave_bytes)
    return {
        "primary_header": prim, "header": img_hdr, "image": image, "flags": flags,
        "flags_header": flags_hdr, "x0": x0, "y0": y0,
        "wcs_wave_hdul": fits.HDUList([fits.PrimaryHDU(), wave_hdu]),
    }
