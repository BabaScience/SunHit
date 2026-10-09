"""Horizon builds and geometry queries. Synchronous; the worker runs them in threads."""

from __future__ import annotations

import logging
from datetime import date

from rasterio.features import rasterize
from shapely import wkb

from sunhit_geo import GEOMETRY_MODEL_VERSION, HorizonProfile, RasterLayer, Spot, build_horizon, compute_segments
from sunhit_geo.horizon import OutsideCoverage
from sunhit_geo.windows import summarize
from sunhit_horizon.db import Db
from sunhit_horizon.surface_cache import Region, SurfaceCache

log = logging.getLogger(__name__)


class JobFailure(Exception):
    """Fails a job with a code the api maps to an HTTP status ("<code>: <message>")."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class GeometryService:
    def __init__(self, db: Db, surface: SurfaceCache):
        self.db = db
        self.surface = surface

    def _own_footprint(self, region: Region, building_id: int | None, like: RasterLayer) -> RasterLayer | None:
        if building_id is None:
            return None
        raw = self.db.footprint_wkb(building_id, region.epsg)
        if raw is None:
            log.warning("building %s not found; ignoring own footprint", building_id)
            return None
        from affine import Affine

        tr = Affine(like.res, 0, like.x0, 0, -like.res, like.y0)
        mask = rasterize([wkb.loads(raw)], out_shape=like.data.shape, transform=tr, fill=0,
                         default_value=1, all_touched=True, dtype="uint8")
        return RasterLayer(mask, like.x0, like.y0, like.res)

    def compute_horizon(self, lat: float, lon: float, height: float, facing: float,
                        building_id: int | None) -> tuple[HorizonProfile, str]:
        found = self.surface.locate(lat, lon)
        if found is None:
            raise OutsideCoverage(f"({lat:.5f}, {lon:.5f}) is outside the surface-model coverage")
        region, x, y = found
        near = self.surface.near_window(region, x, y)
        m = region.manifest
        profile = build_horizon(
            x, y, height, facing,
            ground=near.ground,
            building_height=near.building_height,
            canopy=near.canopy,
            far_terrain=region.far,
            own_footprint=self._own_footprint(region, building_id, near.ground),
            near_radius_m=m.near_radius_m,
            far_radius_m=m.far_radius_m,
        )
        return profile, f"{m.region}/{m.version}"

    # --- jobs -------------------------------------------------------------------------------

    def build_spot_horizon(self, spot_id: str) -> dict:
        spot = self.db.spot(spot_id)
        if spot is None:
            raise JobFailure("not_found", f"spot {spot_id} not found")
        try:
            profile, version = self.compute_horizon(
                spot.lat, spot.lon, spot.height_above_ground_m, spot.facing_azimuth_deg, spot.building_id
            )
        except OutsideCoverage as e:
            self.db.set_status(spot_id, "outside_coverage", str(e))
            return {"spot_id": spot_id, "status": "outside_coverage"}
        self.db.save_horizon(spot_id, profile.to_dict(), version)
        return {"spot_id": spot_id, "status": "ready", "surface_version": version}

    def windows(self, spot_id: str, dates: list[str]) -> dict:
        spot = self.db.spot(spot_id)
        if spot is None:
            raise JobFailure("not_found", f"spot {spot_id} not found")
        stored = self.db.horizon(spot_id)
        if stored is None:
            raise JobFailure("not_ready", f"horizon for {spot_id} is {spot.horizon_status}")
        profile, confidence = stored
        s = Spot(spot.lat, spot.lon, spot.height_above_ground_m, spot.tilt_deg, spot.facing_azimuth_deg, spot.timezone)
        result = windows_response(s, HorizonProfile.from_dict(profile), dates, confidence)
        result["spot_id"] = spot_id
        return result

    def point(self, p: dict) -> dict:
        building_id = self.db.nearest_building(p["lat"], p["lon"])
        try:
            profile, _ = self.compute_horizon(
                p["lat"], p["lon"], p["height_above_ground_m"], p["facing_azimuth_deg"], building_id
            )
        except OutsideCoverage as e:
            raise JobFailure("outside_coverage", str(e)) from e
        s = Spot(p["lat"], p["lon"], p["height_above_ground_m"], p["tilt_deg"], p["facing_azimuth_deg"], p["timezone"])
        return windows_response(s, profile, p["dates"], "modelled")


def windows_response(spot: Spot, horizon: HorizonProfile, dates: list[str], confidence: str) -> dict:
    days = []
    for d in dates:
        segs = compute_segments(spot, horizon, date.fromisoformat(d))
        seg_dicts = [s.to_dict(spot.timezone) for s in segs]
        days.append({
            "date": d,
            "segments": seg_dicts,
            "geometric_windows": [s for s in seg_dicts if s["type"] == "sun"],
            **summarize(segs),
        })
    return {
        "timezone": spot.timezone,
        "model_version": GEOMETRY_MODEL_VERSION,
        "horizon_confidence": confidence,
        "days": days,
    }
