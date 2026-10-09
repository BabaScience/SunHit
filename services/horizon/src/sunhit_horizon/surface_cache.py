"""Local cache of the published surface model, and windowed reads around a spot."""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.windows import Window

from sunhit_geo import RasterLayer
from sunhit_geo.surface import FAR_LAYERS, NEAR_LAYERS, Manifest, ObjectStore

log = logging.getLogger(__name__)
REFRESH_S = 60.0


@dataclass
class Region:
    manifest: Manifest
    dir: Path
    to_xy: Transformer
    far: RasterLayer

    @property
    def epsg(self) -> int:
        return int(self.manifest.crs.split(":")[1])

    def path(self, layer: str) -> Path:
        return self.dir / f"{layer}.tif"


@dataclass
class NearWindow:
    ground: RasterLayer
    building_height: RasterLayer
    building_height_source: RasterLayer | None  # None for surfaces built before height sources
    canopy: RasterLayer


class SurfaceCache:
    def __init__(self, cache_dir: Path, store: ObjectStore | None = None):
        self.cache_dir = cache_dir
        self.store = store or ObjectStore()
        self.regions: dict[str, Region] = {}
        self._checked = 0.0
        self._lock = threading.Lock()

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            if not force and time.monotonic() - self._checked < REFRESH_S:
                return
            self._checked = time.monotonic()
            try:
                names = self.store.regions()
            except Exception as e:  # store down: keep serving what we have
                log.warning("surface refresh failed: %s", e)
                return
            for name in names:
                try:
                    m = self.store.current_manifest(name)
                except Exception as e:
                    log.warning("no manifest for region %s: %s", name, e)
                    continue
                cur = self.regions.get(name)
                if cur and cur.manifest.version == m.version:
                    continue
                self.regions[name] = self._load(m)
                log.info("surface %s/%s loaded", name, m.version)

    def _load(self, m: Manifest) -> Region:
        d = self.cache_dir / m.region / m.version
        for layer in NEAR_LAYERS + FAR_LAYERS:
            f = d / f"{layer}.tif"
            if not f.exists():
                log.info("downloading %s/%s/%s.tif", m.region, m.version, layer)
                try:
                    self.store.download(f"{m.region}/{m.version}/{layer}.tif", f)
                except Exception:
                    if layer != "near_building_src":
                        raise
                    log.warning("%s/%s has no height-source layer", m.region, m.version)
        with rasterio.open(d / "far_terrain.tif") as src:
            far = RasterLayer(src.read(1).astype(np.float32), src.transform.c, src.transform.f, src.transform.a)
        return Region(m, d, Transformer.from_crs("EPSG:4326", m.crs, always_xy=True), far)

    def locate(self, lat: float, lon: float) -> tuple[Region, float, float] | None:
        """Region whose core contains the point, with projected coordinates."""
        self.refresh()
        for r in self.regions.values():
            x, y = r.to_xy.transform(lon, lat)
            if r.manifest.core_bounds.contains(x, y):
                return r, x, y
        return None

    def near_window(self, region: Region, x: float, y: float) -> NearWindow:
        """All near layers in a square of near_radius_m around (x, y), on the region grid."""
        m = region.manifest
        res = m.near_res_m
        r = m.near_radius_m + 2 * res
        col0 = math.floor((x - r - m.near_bounds.left) / res)
        row0 = math.floor((m.near_bounds.top - (y + r)) / res)
        size = math.ceil(2 * r / res)
        win = Window(col0, row0, size, size)
        x0 = m.near_bounds.left + col0 * res
        y0 = m.near_bounds.top - row0 * res

        def read(layer: str, fill: float) -> RasterLayer:
            with rasterio.open(region.path(layer)) as src:
                a = src.read(1, window=win, boundless=True, fill_value=fill).astype(np.float32)
            a[~np.isfinite(a)] = fill if np.isfinite(fill) else np.nan
            return RasterLayer(a, x0, y0, res)

        src = None
        if region.path("near_building_src").exists():
            src = read("near_building_src", 0.0)
            src = RasterLayer(src.data.astype(np.uint8), src.x0, src.y0, src.res)
        return NearWindow(read("near_ground", np.nan), read("near_building_h", 0.0), src, read("near_canopy", 0.0))
