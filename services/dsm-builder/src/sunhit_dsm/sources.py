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

# Quality scale shared by all layers (0-100). User calibration will sit above LiDAR.
Q_USER, Q_LIDAR, Q_OSM_HEIGHT, Q_RASTER, Q_OSM_LEVELS, Q_DEFAULT = 100, 90, 70, 60, 55, 10


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
            return float(m.group(1).replace(",", ".")), "tag:height", Q_OSM_HEIGHT
    levels = tags.get("building:levels")
    if levels:
        m = _NUM.match(levels)
        if m:
            n = float(m.group(1).replace(",", "."))
            roof = tags.get("roof:levels")
            extra = 0.5 * float(roof) if roof and roof.replace(".", "", 1).isdigit() else 0.0
            return (n + extra) * level_height_m, "tag:levels", Q_OSM_LEVELS
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
