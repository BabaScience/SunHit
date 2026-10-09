"""Pluggable surface sources.

Every source returns height values on a target grid plus a quality score; the fused surface takes,
per pixel, the highest-quality source that has data. New sources (national LiDAR, Urban Atlas,
user calibration) implement the same interface without touching the builder.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.warp import Resampling, reproject
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import linemerge, polygonize, transform, unary_union

# Quality scale shared by all layers (0-100); per pixel, the highest wins.
# Building heights, in the priority order of decision 0002:
Q_USER = 100  # user calibration (later)
Q_LIDAR = 90  # national / regional LiDAR (parked)
Q_CTRN = 80  # regional technical map, surveyed heights
Q_OSM_HEIGHT = 70  # OSM height tag
Q_RASTER = 60  # any extra building-height raster from the region config
Q_OSM_LEVELS = 55  # OSM building:levels x level height
Q_URBAN_ATLAS = 40  # Urban Atlas 2012 block heights, only inside known footprints
Q_DEFAULT = 10  # footprint with no height information

# Per-pixel height-source codes written to near_building_src.tif (0 = no building).
HEIGHT_SOURCE_BY_QUALITY = {
    Q_USER: 6, Q_LIDAR: 7, Q_CTRN: 1, Q_OSM_HEIGHT: 2, Q_RASTER: 8, Q_OSM_LEVELS: 3,
    Q_URBAN_ATLAS: 4, Q_DEFAULT: 5,
}


@dataclass
class Grid:
    crs: str
    transform: Affine
    shape: tuple[int, int]  # rows, cols


@dataclass
class SourceRaster:
    values: np.ndarray  # float32, NaN where the source has no data
    quality: np.ndarray  # uint8, 0 where the source has no data


class SurfaceSource(Protocol):
    name: str
    layer: str  # "terrain" | "buildings" | "canopy"
    license: str
    commercial_ok: bool

    def read(self, grid: Grid) -> SourceRaster: ...


def fuse(sources: list[SurfaceSource], grid: Grid, fill: float = np.nan) -> tuple[np.ndarray, np.ndarray]:
    """Highest quality wins per pixel. Returns (values, winning quality)."""
    values = np.full(grid.shape, fill, dtype=np.float32)
    best = np.zeros(grid.shape, dtype=np.uint8)
    for src in sources:
        r = src.read(grid)
        take = (r.quality > best) & np.isfinite(r.values)
        values[take] = r.values[take]
        best[take] = r.quality[take]
    return values, best


def _reproject(path: Path, grid: Grid, resampling: Resampling) -> np.ndarray:
    out = np.full(grid.shape, np.nan, dtype=np.float32)
    with rasterio.open(path) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=out,
            src_nodata=src.nodata,
            dst_transform=grid.transform,
            dst_crs=grid.crs,
            dst_nodata=np.nan,
            resampling=resampling,
        )
    return out


@dataclass
class RasterSource:
    """Any single-band raster: terrain, canopy, or building height above ground."""

    name: str
    layer: str
    path: Path
    quality: int
    license: str
    commercial_ok: bool
    resampling: Resampling = Resampling.bilinear
    min_value: float | None = None  # values below become "no data" (e.g. heights of 0)

    def read(self, grid: Grid) -> SourceRaster:
        v = _reproject(self.path, grid, self.resampling)
        if self.min_value is not None:
            v[v < self.min_value] = np.nan
        q = np.where(np.isfinite(v), self.quality, 0).astype(np.uint8)
        return SourceRaster(v, q)


# --- OpenStreetMap buildings ---------------------------------------------------------------

_NUM = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*(m|meters|metres)?\s*$", re.I)


def parse_height(tags: dict, level_height_m: float) -> tuple[float | None, str, int]:
    """Height above ground from OSM tags: (height, height_source, quality)."""
    h = tags.get("height") or tags.get("building:height")
    if h:
        m = _NUM.match(h)
        if m:
            return float(m.group(1).replace(",", ".")), "osm_height", Q_OSM_HEIGHT
    levels = tags.get("building:levels")
    if levels:
        m = _NUM.match(levels)
        if m:
            n = float(m.group(1).replace(",", "."))
            roof = tags.get("roof:levels")
            extra = 0.5 * float(roof) if roof and roof.replace(".", "", 1).isdigit() else 0.0
            return (n + extra) * level_height_m, "osm_levels", Q_OSM_LEVELS
    return None, "default", Q_DEFAULT


def _ring_polygons(lines) -> list[Polygon]:
    merged = linemerge(lines) if lines else None
    if merged is None:
        return []
    return [p for p in polygonize(merged) if p.is_valid and p.area > 0]


@dataclass
class Building:
    id: int
    source_ref: str
    geom: MultiPolygon  # EPSG:4326
    height_m: float
    height_source: str
    quality: int


def read_osm_buildings(path: Path, default_height_m: float, level_height_m: float) -> Iterator[Building]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for el in data["elements"]:
        tags = el.get("tags", {})
        if el["type"] == "way":
            coords = [(p["lon"], p["lat"]) for p in el.get("geometry", [])]
            if len(coords) < 4 or coords[0] != coords[-1]:
                continue
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            geom = poly
            bid = el["id"] * 2
        elif el["type"] == "relation":
            outers, inners = [], []
            for m in el.get("members", []):
                if m.get("type") != "way" or "geometry" not in m:
                    continue
                line = shape({"type": "LineString", "coordinates": [(p["lon"], p["lat"]) for p in m["geometry"]]})
                (inners if m.get("role") == "inner" else outers).append(line)
            outer = unary_union(_ring_polygons(outers))
            if outer.is_empty:
                continue
            inner = unary_union(_ring_polygons(inners))
            geom = outer.difference(inner) if not inner.is_empty else outer
            bid = el["id"] * 2 + 1
        else:
            continue
        if geom.is_empty:
            continue
        if isinstance(geom, Polygon):
            geom = MultiPolygon([geom])
        elif not isinstance(geom, MultiPolygon):
            geom = MultiPolygon([g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)])
            if geom.is_empty:
                continue
        height, hsrc, q = parse_height(tags, level_height_m)
        if height is None:
            height = 4.0 if tags.get("building") == "roof" else default_height_m
        yield Building(bid, f"{el['type']}/{el['id']}", geom, float(height), hsrc, q)


@dataclass
class OsmBuildingsSource:
    """Rasterizes OSM footprints; height and quality come from each building's tags."""

    buildings: list[Building]
    name: str = "osm"
    layer: str = "buildings"
    license: str = "ODbL-1.0"
    commercial_ok: bool = True

    def read(self, grid: Grid) -> SourceRaster:
        to_grid = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True).transform
        values = np.full(grid.shape, np.nan, dtype=np.float32)
        quality = np.zeros(grid.shape, dtype=np.uint8)
        # Rasterize lower quality first so better-known buildings win where footprints overlap.
        for q in sorted({b.quality for b in self.buildings}):
            shapes = [(transform(to_grid, b.geom), b.height_m) for b in self.buildings if b.quality == q]
            if not shapes:
                continue
            h = rasterize(shapes, out_shape=grid.shape, transform=grid.transform, fill=np.nan, dtype="float32")
            hit = np.isfinite(h)
            values[hit] = h[hit]
            quality[hit] = q
        return SourceRaster(values, quality)


# --- Regional technical map volumes (CTRN / DBGT UN_VOL) ----------------------------------

@dataclass
class CtrnVolumesSource:
    """Surveyed volumetric units with their height above ground (UN_VOL_AV).

    Volumes split a building into parts of different height, so they are rasterized as they are;
    where volumes overlap (elevated parts), the tallest wins.
    """

    paths: list[Path]
    crs: str = "EPSG:25833"
    name: str = "ctrn"
    layer: str = "buildings"
    license: str = "CC-BY-4.0"
    commercial_ok: bool = True
    height_field: str = "UN_VOL_AV"

    def read(self, grid: Grid) -> SourceRaster:
        import pyogrio.raw
        import shapely

        to_grid = Transformer.from_crs(self.crs, grid.crs, always_xy=True)
        # Grid bounds in the source CRS, to read only what we need.
        left, top = grid.transform.c, grid.transform.f
        right = left + grid.transform.a * grid.shape[1]
        bottom = top + grid.transform.e * grid.shape[0]
        back = Transformer.from_crs(grid.crs, self.crs, always_xy=True)
        xs, ys = back.transform([left, right, left, right], [bottom, bottom, top, top])
        bbox = (min(xs), min(ys), max(xs), max(ys))

        geoms, heights = [], []
        for path in self.paths:
            _, _, wkb, fields = pyogrio.raw.read(path, columns=[self.height_field], bbox=bbox)
            g = shapely.force_2d(shapely.from_wkb(wkb))
            h = np.asarray(fields[0], dtype=np.float64)
            ok = np.isfinite(h) & (h > 0) & ~shapely.is_empty(g)
            geoms.extend(g[ok])
            heights.extend(h[ok])
        print(f"  ctrn: {len(geoms)} volumes in the grid")
        order = np.argsort(heights)  # later shapes overwrite: tallest last
        shapes = ((transform(to_grid.transform, geoms[i]), float(heights[i])) for i in order)
        values = rasterize(shapes, out_shape=grid.shape, transform=grid.transform, fill=np.nan, dtype="float32")
        quality = np.where(np.isfinite(values), Q_CTRN, 0).astype(np.uint8)
        return SourceRaster(values, quality)
