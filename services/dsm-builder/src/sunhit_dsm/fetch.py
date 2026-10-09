"""Download small open-data samples for a region (Phase 0: Rome).

Everything lands in <out>/raw with a SOURCES.md listing licences and attribution.
Sources:
  TINITALY 1.1 DTM (INGV, CC BY 4.0)          near terrain at 10 m, far terrain at about 30 m (WCS)
  Copernicus GLO-30 DSM (ESA, free)             far-terrain fallback where TINITALY has no data
  Meta/WRI canopy height v2 (CC BY 4.0)         about 1 m, read as a window from the public COGs
  OpenStreetMap buildings (ODbL)                footprints and height tags, via Overpass
  Building heights, per region config           e.g. Lazio CTRN (CC BY 4.0), Urban Atlas 2012 (EEA)
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import rasterio
import requests
from pyproj import Transformer
from rasterio.io import MemoryFile
from rasterio.merge import merge
from rasterio.windows import from_bounds

from sunhit_dsm.heights import fetch_ctrn_lazio, fetch_urban_atlas
from sunhit_geo.surface import Bounds, RegionConfig

TINITALY_WCS = (
    "http://tinitaly.pi.ingv.it/TINItaly_1_1/wcs?service=WCS&version=2.0.1&request=GetCoverage"
    "&coverageId=TINItaly_1_1__tinitaly_dem&format=image/tiff"
    "&subsettingCrs=http://www.opengis.net/def/crs/EPSG/0/4326"
)
GLO30_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif"
)
CANOPY_URL = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/chm/{qk}.tif"
CANOPY_ZOOM = 10
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "sunhit-fetch-samples/0.1 (open-data research; contact: project maintainer)"

SOURCES_MD = """# Raw sample sources ({region})

| File | Source | Licence | Attribution |
|---|---|---|---|
| tinitaly_near.tif, tinitaly_far.tif | TINITALY 1.1 DTM, INGV | CC BY 4.0 | Tarquini S. et al. (2023). TINITALY, a digital elevation model of Italy with a 10 meters cell size (Version 1.1). INGV. https://doi.org/10.13127/tinitaly/1.1 |
| glo30_far.tif | Copernicus DEM GLO-30 | Copernicus DEM licence (free, commercial use allowed) | © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA |
| canopy_near.tif | Meta & WRI High Resolution Canopy Height v2 | CC BY 4.0 | Meta / World Resources Institute, Global Canopy Height |
| osm_buildings.json | OpenStreetMap | ODbL 1.0 | © OpenStreetMap contributors |
| ctrn/*_UN_VOL.* | Regione Lazio, Carta Tecnica Regionale Numerica 2014 1:5,000 (DBGT) | CC BY 4.0 | © Regione Lazio |
| urban_atlas_height.tif | Copernicus Land Monitoring Service, Urban Atlas Building Height 2012 | EEA re-use policy (free, commercial use allowed, acknowledge source) | © European Union, Copernicus Land Monitoring Service, European Environment Agency (EEA) |
"""


_keep: list[MemoryFile] = []


def to_wgs84_bbox(b: Bounds, crs: str, pad_deg: float = 0.002) -> tuple[float, float, float, float]:
    t = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    xs, ys = [], []
    for x in np.linspace(b.left, b.right, 9):
        for y in np.linspace(b.bottom, b.top, 9):
            lon, lat = t.transform(x, y)
            xs.append(lon)
            ys.append(lat)
    return min(xs) - pad_deg, min(ys) - pad_deg, max(xs) + pad_deg, max(ys) + pad_deg


def _get(url: str, *, retries: int = 4, **kw) -> requests.Response:
    for attempt in range(retries):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=300, **kw)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code}")
            r.raise_for_status()
        except (requests.ConnectionError, requests.HTTPError, requests.Timeout) as e:
            if attempt == retries - 1:
                raise
            wait = 10 * (attempt + 1)
            print(f"  retry in {wait}s ({e})")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_tinitaly(bbox, out: Path, chunks: int, scale: float | None) -> None:
    """WCS GetCoverage in lat/lon chunks, mosaicked into one GeoTIFF (EPSG:4326 grid)."""
    if out.exists():
        print(f"  {out.name} exists, skipping")
        return
    w, s, e, n = bbox
    lons = np.linspace(w, e, chunks + 1)
    lats = np.linspace(s, n, chunks + 1)
    datasets = []
    for i in range(chunks):
        for j in range(chunks):
            url = (
                f"{TINITALY_WCS}&subset=Long({lons[i]:.6f},{lons[i + 1]:.6f})"
                f"&subset=Lat({lats[j]:.6f},{lats[j + 1]:.6f})"
            )
            if scale:
                url += f"&SCALEFACTOR={scale}"
            print(f"  tinitaly chunk {i * chunks + j + 1}/{chunks * chunks}")
            r = _get(url)
            if not r.headers.get("content-type", "").startswith("image/tiff"):
                raise RuntimeError(f"unexpected WCS response: {r.text[:300]}")
            mem = MemoryFile(r.content)
            _keep.append(mem)  # the dataset is only readable while its MemoryFile is alive
            datasets.append(mem.open())
    mosaic, transform = merge(datasets, nodata=-9999.0)
    profile = datasets[0].profile | {
        "driver": "GTiff", "height": mosaic.shape[1], "width": mosaic.shape[2], "transform": transform,
        "nodata": -9999.0, "compress": "deflate", "predictor": 3, "tiled": True,
        "dtype": "float32",
    }
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(mosaic.astype("float32"))


def fetch_glo30(bbox, out: Path) -> None:
    if out.exists():
        print(f"  {out.name} exists, skipping")
        return
    w, s, e, n = bbox
    datasets = []
    for lat in range(math.floor(s), math.floor(n) + 1):
        for lon in range(math.floor(w), math.floor(e) + 1):
            url = GLO30_URL.format(
                ns="N" if lat >= 0 else "S", lat=abs(lat), ew="E" if lon >= 0 else "W", lon=abs(lon)
            )
            if requests.head(url, timeout=60).status_code != 200:
                print(f"  glo30 tile missing (sea?) {lat},{lon}")
                continue
            src = rasterio.open(f"/vsicurl/{url}")
            win = from_bounds(max(w, lon), max(s, lat), min(e, lon + 1), min(n, lat + 1), src.transform)
            data = src.read(1, window=win, boundless=True, fill_value=-9999.0)
            tr = src.window_transform(win)
            mem = MemoryFile()
            _keep.append(mem)
            ds = mem.open(driver="GTiff", height=data.shape[0], width=data.shape[1], count=1,
                          dtype="float32", crs=src.crs, transform=tr, nodata=-9999.0)
            ds.write(data.astype("float32"), 1)
            datasets.append(ds)
            print(f"  glo30 tile {lat},{lon}: {data.shape}")
    mosaic, transform = merge(datasets, nodata=-9999.0)
    with rasterio.open(out, "w", driver="GTiff", height=mosaic.shape[1], width=mosaic.shape[2], count=1,
                       dtype="float32", crs="EPSG:4326", transform=transform, nodata=-9999.0,
                       compress="deflate", predictor=3, tiled=True) as dst:
        dst.write(mosaic.astype("float32"))


def _quadkey(x: int, y: int, z: int) -> str:
    out = ""
    for i in range(z, 0, -1):
        m = 1 << (i - 1)
        out += str((1 if x & m else 0) + (2 if y & m else 0))
    return out


def _tile_xy(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def fetch_canopy(bbox, out: Path) -> None:
    """Window of the public canopy COGs (EPSG:3857, Byte metres) covering the bbox."""
    if out.exists():
        print(f"  {out.name} exists, skipping")
        return
    w, s, e, n = bbox
    x0, y0 = _tile_xy(w, n, CANOPY_ZOOM)
    x1, y1 = _tile_xy(e, s, CANOPY_ZOOM)
    to_merc = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    mx0, my0 = to_merc.transform(w, s)
    mx1, my1 = to_merc.transform(e, n)
    datasets = []
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            qk = _quadkey(tx, ty, CANOPY_ZOOM)
            url = CANOPY_URL.format(qk=qk)
            src = rasterio.open(f"/vsicurl/{url}")
            b = src.bounds
            ib = (max(mx0, b.left), max(my0, b.bottom), min(mx1, b.right), min(my1, b.top))
            if ib[0] >= ib[2] or ib[1] >= ib[3]:
                continue
            win = from_bounds(*ib, src.transform).round_offsets().round_lengths()
            data = src.read(1, window=win)
            mem = MemoryFile()
            _keep.append(mem)
            ds = mem.open(driver="GTiff", height=data.shape[0], width=data.shape[1], count=1,
                          dtype=data.dtype, crs=src.crs, transform=src.window_transform(win), nodata=src.nodata)
            ds.write(data, 1)
            datasets.append(ds)
            print(f"  canopy tile {qk}: {data.shape}")
    mosaic, transform = merge(datasets)
    with rasterio.open(out, "w", driver="GTiff", height=mosaic.shape[1], width=mosaic.shape[2], count=1,
                       dtype=mosaic.dtype, crs="EPSG:3857", transform=transform,
                       nodata=datasets[0].nodata, compress="deflate", tiled=True) as dst:
        dst.write(mosaic)


def fetch_osm_buildings(bbox, out: Path, chunks: int) -> None:
    """Building ways and multipolygon relations with full geometry, chunked to keep Overpass happy."""
    if out.exists():
        print(f"  {out.name} exists, skipping")
        return
    w, s, e, n = bbox
    lons = np.linspace(w, e, chunks + 1)
    lats = np.linspace(s, n, chunks + 1)
    elements: dict[str, dict] = {}
    for i in range(chunks):
        for j in range(chunks):
            bb = f"{lats[j]:.6f},{lons[i]:.6f},{lats[j + 1]:.6f},{lons[i + 1]:.6f}"
            q = (
                f'[out:json][timeout:600];(way["building"]({bb});'
                f'relation["building"]["type"="multipolygon"]({bb}););out tags geom;'
            )
            print(f"  overpass chunk {i * chunks + j + 1}/{chunks * chunks}")
            for attempt in range(5):
                r = requests.post(OVERPASS_URL, data={"data": q}, headers={"User-Agent": USER_AGENT}, timeout=900)
                if r.status_code == 200:
                    break
                wait = 30 * (attempt + 1)
                print(f"  overpass {r.status_code}, retry in {wait}s")
                time.sleep(wait)
            r.raise_for_status()
            for el in r.json()["elements"]:
                elements[f"{el['type']}/{el['id']}"] = el
            time.sleep(2)
    out.write_text(json.dumps({"elements": list(elements.values())}), encoding="utf-8")
    print(f"  {len(elements)} OSM building elements")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="infra/regions/rome.json")
    ap.add_argument("--data-dir", default="data/samples")
    args = ap.parse_args()

    region = RegionConfig.load(args.region)
    raw = Path(args.data_dir) / region.name / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    near = to_wgs84_bbox(region.near_bounds(), region.crs)
    far = to_wgs84_bbox(region.far_bounds(), region.crs, pad_deg=0.01)
    print(f"region {region.name}: near bbox {near}, far bbox {far}")

    print("TINITALY near (10 m)")
    fetch_tinitaly(near, raw / "tinitaly_near.tif", chunks=2, scale=None)
    print("TINITALY far (about 30 m)")
    fetch_tinitaly(far, raw / "tinitaly_far.tif", chunks=4, scale=round(10.0 / region.far_res_m, 4))
    print("Copernicus GLO-30 far (fallback)")
    fetch_glo30(far, raw / "glo30_far.tif")
    print("Canopy height (about 1 m)")
    fetch_canopy(near, raw / "canopy_near.tif")
    print("OSM buildings")
    fetch_osm_buildings(near, raw / "osm_buildings.json", chunks=3)

    for hs in region.building_height_sources:
        if hs["type"] == "ctrn_lazio":
            print(f"CTRN Lazio ({hs['province']}): volumetric units with heights")
            fetch_ctrn_lazio(hs["province"], region.near_bounds(), region.crs, raw / "ctrn")
        elif hs["type"] == "urban_atlas_2012":
            print("Urban Atlas Building Height 2012 (10 m)")
            fetch_urban_atlas(region.near_bounds(), region.crs, raw / "urban_atlas_height.tif")
        else:
            raise ValueError(f"unknown building height source {hs['type']}")

    (raw / "SOURCES.md").write_text(SOURCES_MD.format(region=region.name), encoding="utf-8")
    print(f"done: {raw}")


if __name__ == "__main__":
    main()
