"""Building-height adapters (region by region).

Priority when fusing, highest first (see docs/decisions/0002):
  1. user calibration (later)  2. regional technical map (CTRN)  3. OSM height tag
  4. OSM building:levels       5. Urban Atlas Building Height 2012  6. default height

Adapters here download raw data only; sources.py turns it into rasters.
"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import numpy as np
import rasterio
import requests
from pyproj import Transformer

from sunhit_geo.surface import Bounds

USER_AGENT = "sunhit-fetch-samples/0.1 (open-data research)"


class HttpRangeFile(io.RawIOBase):
    """Seekable read-only file over HTTP range requests, so zipfile can pull single members out of
    archives of hundreds of MB without downloading them."""

    def __init__(self, url: str, headers: dict, size: int):
        self.url, self.headers, self.size, self.pos = url, headers, size, 0
        self.fetched = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        self.pos = {io.SEEK_SET: offset, io.SEEK_CUR: self.pos + offset, io.SEEK_END: self.size + offset}[whence]
        return self.pos

    def readinto(self, b):
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        r = requests.get(self.url, headers=self.headers | {"Range": f"bytes={self.pos}-{self.pos + n - 1}"},
                         timeout=300)
        if r.status_code != 206:
            raise OSError(f"range request failed: HTTP {r.status_code}")
        data = r.content
        b[: len(data)] = data
        self.pos += len(data)
        self.fetched += len(data)
        return len(data)


# --- Regione Lazio CTRN 2014 (DBGT 1:5,000), CC BY 4.0 -------------------------------------

CTRN_LAZIO_BASE = "https://geoportale.regione.lazio.it/cartografia"
CTRN_LAZIO_DIR = "2014_DBGT_5K_SHP/strati-informativi/{province}"
CTRN_LAYER = "UN_VOL"  # volumetric units: UN_VOL_AV = height above ground (m), UN_VOL_QE = top elevation
CTRN_CRS = "EPSG:25833"
CTRN_PARTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")


def _shp_bbox(zf: zipfile.ZipFile, member: str) -> tuple[float, float, float, float]:
    """Bounding box from the 100-byte shapefile header (bytes 36-67: xmin, ymin, xmax, ymax)."""
    with zf.open(member) as f:
        head = f.read(100)
    return struct.unpack("<4d", head[36:68])


def fetch_ctrn_lazio(province: str, bounds: Bounds, crs: str, out_dir: Path) -> list[Path]:
    """Extract UN_VOL from every CTRN sheet that intersects `bounds`. Returns extracted .shp paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    token = requests.post(f"{CTRN_LAZIO_BASE}/api/login", data="{}", timeout=60).text
    headers = {"X-Auth": token, "User-Agent": USER_AGENT}
    folder = CTRN_LAZIO_DIR.format(province=province)
    listing = requests.get(f"{CTRN_LAZIO_BASE}/api/resources/{folder}/", headers=headers, timeout=60).json()

    to_ctrn = Transformer.from_crs(crs, CTRN_CRS, always_xy=True)
    xs, ys = to_ctrn.transform([bounds.left, bounds.right, bounds.left, bounds.right],
                               [bounds.bottom, bounds.bottom, bounds.top, bounds.top])
    want = (min(xs), min(ys), max(xs), max(ys))

    found = []
    for item in sorted(listing["items"], key=lambda i: i["name"]):
        if not item["name"].endswith(".zip"):
            continue
        sheet = item["name"].removesuffix(".zip")
        done = out_dir / f"{sheet}_{CTRN_LAYER}.shp"
        if done.exists():
            found.append(done)
            continue
        url = f"{CTRN_LAZIO_BASE}/api/raw/{folder}/{item['name']}"
        raw = HttpRangeFile(url, headers, item["size"])
        zf = zipfile.ZipFile(io.BufferedReader(raw, buffer_size=1 << 20))
        member = next((n for n in zf.namelist() if n.endswith(f"/{CTRN_LAYER}.shp")), None)
        if member is None:
            continue
        x0, y0, x1, y1 = _shp_bbox(zf, member)
        if x1 < want[0] or x0 > want[2] or y1 < want[1] or y0 > want[3]:
            print(f"  {sheet}: outside the region")
            continue
        for ext in CTRN_PARTS:
            m = member[:-4] + ext
            if m in zf.namelist():
                (out_dir / f"{sheet}_{CTRN_LAYER}{ext}").write_bytes(zf.read(m))
        print(f"  {sheet}: {CTRN_LAYER} extracted ({raw.fetched / 1e6:.1f} MB read of {item['size'] / 1e6:.0f} MB)")
        found.append(done)
    return found


# --- Urban Atlas Building Height 2012 (10 m), EEA re-use policy -----------------------------

UA_URL = "https://image.discomap.eea.europa.eu/arcgis/rest/services/UrbanAtlas/UA_BuildingHeights_2012_10m/ImageServer/exportImage"
UA_CRS = "EPSG:3035"
UA_NODATA = 65535


def fetch_urban_atlas(bounds: Bounds, crs: str, out: Path, tile_px: int = 2000) -> None:
    """Building block heights (m) at 10 m, from the EEA image service, in its native EPSG:3035."""
    if out.exists():
        print(f"  {out.name} exists, skipping")
        return
    to_ua = Transformer.from_crs(crs, UA_CRS, always_xy=True)
    xs, ys = to_ua.transform([bounds.left, bounds.right, bounds.left, bounds.right],
                             [bounds.bottom, bounds.bottom, bounds.top, bounds.top])
    x0, y0 = np.floor(min(xs) / 10) * 10, np.floor(min(ys) / 10) * 10
    x1, y1 = np.ceil(max(xs) / 10) * 10, np.ceil(max(ys) / 10) * 10
    w, h = int((x1 - x0) / 10), int((y1 - y0) / 10)
    data = np.full((h, w), UA_NODATA, dtype=np.uint16)
    for ty in range(0, h, tile_px):
        for tx in range(0, w, tile_px):
            bx0, by1 = x0 + tx * 10, y1 - ty * 10
            tw, th = min(tile_px, w - tx), min(tile_px, h - ty)
            params = {
                "bbox": f"{bx0},{by1 - th * 10},{bx0 + tw * 10},{by1}",
                "bboxSR": 3035, "imageSR": 3035, "size": f"{tw},{th}",
                "format": "tiff", "pixelType": "U16", "noData": UA_NODATA,
                "interpolation": "RSP_NearestNeighbor", "f": "image",
            }
            r = requests.get(UA_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=300)
            r.raise_for_status()
            with rasterio.MemoryFile(r.content) as mem, mem.open() as src:
                data[ty:ty + th, tx:tx + tw] = src.read(1)[:th, :tw]
            print(f"  urban atlas tile {ty // tile_px},{tx // tile_px}")
    with rasterio.open(out, "w", driver="GTiff", height=h, width=w, count=1, dtype="uint16", crs=UA_CRS,
                       transform=rasterio.transform.from_origin(x0, y1, 10, 10), nodata=UA_NODATA,
                       compress="deflate", tiled=True) as dst:
        dst.write(data, 1)
    valid = data[(data != UA_NODATA) & (data > 0)]
    print(f"  urban atlas: {valid.size} building pixels, median {np.median(valid) if valid.size else 0} m")
