"""Layer B: per-spot horizon profile by ray-casting over the fused surface model.

All rasters share one projected CRS in metres (UTM). Rays are cast every `azimuth_step_deg`
over the near layers (terrain + buildings + canopy, out to `near_radius_m`) and then over the
coarse far-terrain layer (out to `far_radius_m`) with earth-curvature and refraction correction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numba import njit

SOURCE_NONE, SOURCE_TERRAIN, SOURCE_BUILDING, SOURCE_TREE, SOURCE_USER = 0, 1, 2, 3, 4
SOURCE_NAMES = {
    SOURCE_NONE: "none",
    SOURCE_TERRAIN: "terrain",
    SOURCE_BUILDING: "building",
    SOURCE_TREE: "tree",
    SOURCE_USER: "user",
}

# Where a building's height came from (near_building_src.tif codes). "default" heights are a guess:
# horizons that rely on them are low confidence and the first candidates for calibration.
HEIGHT_SOURCE_NAMES = {
    0: None,
    1: "ctrn",
    2: "osm_height",
    3: "osm_levels",
    4: "urban_atlas",
    5: "default",
    6: "user",
    7: "lidar",
    8: "raster",
}
HEIGHT_SOURCE_DEFAULT = 5

EARTH_RADIUS_M = 6_371_000.0
REFRACTION_K = 0.13  # standard terrestrial refraction coefficient

KNOWN_LIMITATIONS = [
    "Balconies overhead and other self-shading not present in the surface model are not seen; "
    "calibrate the spot to correct them.",
]


class OutsideCoverage(Exception):
    """The spot is outside the surface-model tiles."""


@dataclass
class RasterLayer:
    """A north-up raster: data[row, col], top-left corner (x0, y0), square pixels of `res` metres."""

    data: np.ndarray
    x0: float
    y0: float
    res: float

    def sample(self, x: float, y: float) -> float:
        col = int(math.floor((x - self.x0) / self.res))
        row = int(math.floor((self.y0 - y) / self.res))
        if 0 <= row < self.data.shape[0] and 0 <= col < self.data.shape[1]:
            return float(self.data[row, col])
        return math.nan


@dataclass
class HorizonProfile:
    azimuth_step_deg: float
    elevation_deg: np.ndarray  # highest solid (terrain/building) obstruction angle per azimuth
    source: np.ndarray  # SOURCE_* code of that obstruction
    distance_m: np.ndarray  # distance to it
    height_source: np.ndarray  # HEIGHT_SOURCE_NAMES code of a building obstruction, 0 otherwise
    canopy_elevation_deg: np.ndarray  # top of canopy above the solid horizon, NaN if none
    ground_elevation_m: float
    eye_elevation_m: float
    confidence: str = "modelled"
    notes: list[str] = field(default_factory=lambda: list(KNOWN_LIMITATIONS))

    @property
    def default_height_share(self) -> float:
        """Share of building-obstructed azimuths whose building height is the default guess."""
        bld = self.source == SOURCE_BUILDING
        if not bld.any():
            return 0.0
        return float((self.height_source[bld] == HEIGHT_SOURCE_DEFAULT).mean())

    @property
    def azimuths(self) -> np.ndarray:
        return np.arange(len(self.elevation_deg)) * self.azimuth_step_deg

    def to_dict(self) -> dict:
        def clean(a):
            return [None if not np.isfinite(v) else round(float(v), 3) for v in a]

        return {
            "azimuth_step_deg": self.azimuth_step_deg,
            "elevation_deg": clean(self.elevation_deg),
            "source": [SOURCE_NAMES[int(s)] for s in self.source],
            "distance_m": clean(self.distance_m),
            "height_source": [HEIGHT_SOURCE_NAMES.get(int(c)) for c in self.height_source],
            "default_height_share": round(self.default_height_share, 3),
            "canopy_elevation_deg": clean(self.canopy_elevation_deg),
            "ground_elevation_m": round(self.ground_elevation_m, 2),
            "eye_elevation_m": round(self.eye_elevation_m, 2),
            "confidence": self.confidence,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> HorizonProfile:
        codes = {v: k for k, v in SOURCE_NAMES.items()}
        hcodes = {v: k for k, v in HEIGHT_SOURCE_NAMES.items()}
        n = len(d["source"])

        def arr(a):
            return np.array([np.nan if v is None else v for v in a], dtype=np.float64)

        return cls(
            azimuth_step_deg=float(d["azimuth_step_deg"]),
            elevation_deg=arr(d["elevation_deg"]),
            source=np.array([codes[s] for s in d["source"]], dtype=np.uint8),
            distance_m=arr(d["distance_m"]),
            # Profiles stored before height sources existed have no such field.
            height_source=np.array([hcodes.get(v, 0) for v in d.get("height_source", [None] * n)], dtype=np.uint8),
            canopy_elevation_deg=arr(d["canopy_elevation_deg"]),
            ground_elevation_m=float(d["ground_elevation_m"]),
            eye_elevation_m=float(d["eye_elevation_m"]),
            confidence=d.get("confidence", "modelled"),
            notes=list(d.get("notes", [])),
        )

    def interpolate(self, azimuth_deg: np.ndarray):
        """Solid elevation (linear, periodic), source (nearest) and canopy elevation at azimuths."""
        n = len(self.elevation_deg)
        pos = (np.asarray(azimuth_deg, dtype=float) % 360.0) / self.azimuth_step_deg
        i0 = np.floor(pos).astype(int) % n
        i1 = (i0 + 1) % n
        w = pos - np.floor(pos)
        elev = self.elevation_deg[i0] * (1 - w) + self.elevation_deg[i1] * w
        nearest = np.where(w < 0.5, i0, i1)
        source = self.source[nearest]
        c0, c1 = self.canopy_elevation_deg[i0], self.canopy_elevation_deg[i1]
        both = np.isfinite(c0) & np.isfinite(c1)
        canopy = np.where(both, c0 * (1 - w) + c1 * w, self.canopy_elevation_deg[nearest])
        return elev, source, canopy


@njit(cache=True, nogil=True)
def _cast_near(
    ground, bld_h, bld_src, own, canopy, x0, y0, res,
    ox, oy, oz, azimuths, facing, has_own, max_dist, step,
    out_elev, out_src, out_dist, out_hsrc, out_canopy,
):
    nrows, ncols = ground.shape
    curv = (1.0 - REFRACTION_K) / (2.0 * EARTH_RADIUS_M)
    for k in range(azimuths.shape[0]):
        az = azimuths[k]
        a = math.radians(az)
        dx, dy = math.sin(a), math.cos(a)
        # Ignore our own building while the ray is leaving it, but only for rays in front of the
        # surface: rays going back into the building must still be blocked by it.
        diff = abs(((az - facing + 180.0) % 360.0) - 180.0)
        leaving_own = has_own and diff < 90.0
        best, best_src, best_d, best_h = -90.0, 0, math.nan, 0
        best_can = -90.0
        d = step
        while d <= max_dist:
            x = ox + dx * d
            y = oy + dy * d
            col = int(math.floor((x - x0) / res))
            row = int(math.floor((y0 - y) / res))
            if row < 0 or row >= nrows or col < 0 or col >= ncols:
                break
            g = ground[row, col]
            if math.isnan(g):
                d += step
                continue
            drop = d * d * curv
            bh = bld_h[row, col]
            if leaving_own:
                if own[row, col] != 0:
                    bh = 0.0
                else:
                    leaving_own = False
            if bh > 0.0:
                top = g + bh
                src = 2
                hs = bld_src[row, col]
            else:
                top = g
                src = 1
                hs = 0
            ang = math.degrees(math.atan2(top - drop - oz, d))
            if ang > best:
                best, best_src, best_d, best_h = ang, src, d, hs
            if bh <= 0.0:
                c = canopy[row, col]
                if c > 0.0:
                    cang = math.degrees(math.atan2(g + c - drop - oz, d))
                    if cang > best_can:
                        best_can = cang
            d += step
        out_elev[k] = best
        out_src[k] = best_src
        out_dist[k] = best_d
        out_hsrc[k] = best_h
        out_canopy[k] = best_can


@njit(cache=True, nogil=True)
def _cast_far(
    terrain, x0, y0, res, ox, oy, oz, azimuths, min_dist, max_dist, step,
    out_elev, out_src, out_dist, out_hsrc,
):
    nrows, ncols = terrain.shape
    curv = (1.0 - REFRACTION_K) / (2.0 * EARTH_RADIUS_M)
    for k in range(azimuths.shape[0]):
        a = math.radians(azimuths[k])
        dx, dy = math.sin(a), math.cos(a)
        d = min_dist
        while d <= max_dist:
            x = ox + dx * d
            y = oy + dy * d
            col = int(math.floor((x - x0) / res))
            row = int(math.floor((y0 - y) / res))
            if row < 0 or row >= nrows or col < 0 or col >= ncols:
                break
            t = terrain[row, col]
            if not math.isnan(t):
                ang = math.degrees(math.atan2(t - d * d * curv - oz, d))
                if ang > out_elev[k]:
                    out_elev[k] = ang
                    out_src[k] = 1
                    out_dist[k] = d
                    out_hsrc[k] = 0
            d += step


def build_horizon(
    x: float,
    y: float,
    height_above_ground_m: float,
    facing_azimuth_deg: float,
    ground: RasterLayer,
    building_height: RasterLayer,
    canopy: RasterLayer,
    far_terrain: RasterLayer | None = None,
    own_footprint: RasterLayer | None = None,
    building_height_source: RasterLayer | None = None,
    nudge_m: float = 1.0,
    azimuth_step_deg: float = 1.0,
    near_radius_m: float = 2_000.0,
    far_radius_m: float = 50_000.0,
) -> HorizonProfile:
    """Ray-cast the horizon for a spot at projected (x, y).

    The near layers must share the same grid. `building_height` and `canopy` are heights above
    ground in metres (0 = nothing). `own_footprint` is non-zero on the cells of the building the
    spot belongs to.
    """
    if own_footprint is None:
        own_footprint = RasterLayer(np.zeros((1, 1), dtype=np.uint8), ground.x0, ground.y0, ground.res)
        has_own = False
    else:
        has_own = True
    if building_height_source is None:  # unknown: treat every building height as a default guess
        building_height_source = RasterLayer(
            np.where(building_height.data > 0, HEIGHT_SOURCE_DEFAULT, 0).astype(np.uint8),
            ground.x0, ground.y0, ground.res,
        )
    near = [building_height, building_height_source, canopy] + ([own_footprint] if has_own else [])
    for layer in near:
        if layer.data.shape != ground.data.shape or (layer.x0, layer.y0, layer.res) != (
            ground.x0, ground.y0, ground.res,
        ):
            raise ValueError("near layers must share one grid")

    f = math.radians(facing_azimuth_deg)
    ox, oy = x + math.sin(f) * nudge_m, y + math.cos(f) * nudge_m

    g = ground.sample(ox, oy)
    if math.isnan(g) and far_terrain is not None:
        g = far_terrain.sample(ox, oy)
    if math.isnan(g):
        raise OutsideCoverage(f"no terrain at ({x:.1f}, {y:.1f})")
    oz = g + height_above_ground_m

    n = int(round(360.0 / azimuth_step_deg))
    azimuths = np.arange(n, dtype=np.float64) * azimuth_step_deg
    elev = np.full(n, -90.0)
    src = np.zeros(n, dtype=np.uint8)
    dist = np.full(n, np.nan)
    hsrc = np.zeros(n, dtype=np.uint8)
    can = np.full(n, -90.0)

    _cast_near(
        np.ascontiguousarray(ground.data, dtype=np.float32),
        np.ascontiguousarray(building_height.data, dtype=np.float32),
        np.ascontiguousarray(building_height_source.data, dtype=np.uint8),
        np.ascontiguousarray(own_footprint.data, dtype=np.uint8),
        np.ascontiguousarray(canopy.data, dtype=np.float32),
        ground.x0, ground.y0, ground.res,
        ox, oy, oz, azimuths, facing_azimuth_deg % 360.0, has_own,
        near_radius_m, ground.res / 2.0,
        elev, src, dist, hsrc, can,
    )
    if far_terrain is not None:
        _cast_far(
            np.ascontiguousarray(far_terrain.data, dtype=np.float32),
            far_terrain.x0, far_terrain.y0, far_terrain.res,
            ox, oy, oz, azimuths, near_radius_m, far_radius_m, far_terrain.res / 2.0,
            elev, src, dist, hsrc,
        )

    # Canopy only matters where it rises above the solid horizon.
    canopy_elev = np.where(can > elev, can, np.nan)
    return HorizonProfile(
        azimuth_step_deg=azimuth_step_deg,
        elevation_deg=elev,
        source=src,
        distance_m=dist,
        height_source=hsrc,
        canopy_elevation_deg=canopy_elev,
        ground_elevation_m=g,
        eye_elevation_m=oz,
    )


def flat_horizon(azimuth_step_deg: float = 1.0, elevation_deg: float = 0.0) -> HorizonProfile:
    """Unobstructed horizon, used by tests and as a reference."""
    n = int(round(360.0 / azimuth_step_deg))
    return HorizonProfile(
        azimuth_step_deg=azimuth_step_deg,
        elevation_deg=np.full(n, elevation_deg),
        source=np.full(n, SOURCE_NONE, dtype=np.uint8),
        distance_m=np.full(n, np.nan),
        height_source=np.zeros(n, dtype=np.uint8),
        canopy_elevation_deg=np.full(n, np.nan),
        ground_elevation_m=0.0,
        eye_elevation_m=0.0,
        notes=[],
    )
