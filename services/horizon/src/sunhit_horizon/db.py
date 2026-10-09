from __future__ import annotations

import json
import os
from dataclasses import dataclass

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# Same rule as the api: balconies usually sit just outside their footprint.
BUILDING_LOOKUP_RADIUS_M = float(os.environ.get("BUILDING_LOOKUP_RADIUS_M", "5"))


@dataclass
class SpotRow:
    id: str
    lat: float
    lon: float
    height_above_ground_m: float
    tilt_deg: float
    facing_azimuth_deg: float
    timezone: str
    building_id: int | None
    horizon_status: str


class Db:
    def __init__(self, url: str | None = None):
        self.pool = ConnectionPool(
            url or os.environ.get("DATABASE_URL", "postgres://sunhit:sunhit@localhost:5432/sunhit"),
            min_size=1, max_size=4, kwargs={"row_factory": dict_row}, open=True,
        )

    def ping(self) -> bool:
        with self.pool.connection() as c:
            c.execute("SELECT 1")
        return True

    def spot(self, spot_id: str) -> SpotRow | None:
        with self.pool.connection() as c:
            r = c.execute(
                "SELECT id, lat, lon, height_above_ground_m, tilt_deg, facing_azimuth_deg, timezone, "
                "building_id, horizon_status FROM spots WHERE id = %s",
                (spot_id,),
            ).fetchone()
        return SpotRow(**r) if r else None

    def horizon(self, spot_id: str) -> tuple[dict, str] | None:
        with self.pool.connection() as c:
            r = c.execute("SELECT profile, confidence FROM horizons WHERE spot_id = %s", (spot_id,)).fetchone()
        return (r["profile"], r["confidence"]) if r else None

    def save_horizon(self, spot_id: str, profile: dict, surface_version: str) -> None:
        with self.pool.connection() as c:
            c.execute(
                "INSERT INTO horizons (spot_id, surface_version, confidence, profile) VALUES (%s, %s, 'modelled', %s) "
                "ON CONFLICT (spot_id) DO UPDATE SET built_at = now(), surface_version = EXCLUDED.surface_version, "
                "confidence = EXCLUDED.confidence, profile = EXCLUDED.profile",
                (spot_id, surface_version, json.dumps(profile)),
            )
            c.execute(
                "UPDATE spots SET horizon_status = 'ready', horizon_error = NULL, updated_at = now() WHERE id = %s",
                (spot_id,),
            )

    def set_status(self, spot_id: str, status: str, error: str | None = None) -> None:
        with self.pool.connection() as c:
            c.execute(
                "UPDATE spots SET horizon_status = %s, horizon_error = %s, updated_at = now() WHERE id = %s",
                (status, error, spot_id),
            )

    def footprint_wkb(self, building_id: int, epsg: int) -> bytes | None:
        with self.pool.connection() as c:
            r = c.execute(
                "SELECT ST_AsBinary(ST_Transform(geom, %s)) AS wkb FROM buildings WHERE id = %s",
                (epsg, building_id),
            ).fetchone()
        return bytes(r["wkb"]) if r else None

    def nearest_building(self, lat: float, lon: float) -> int | None:
        with self.pool.connection() as c:
            r = c.execute(
                "SELECT b.id FROM buildings b "
                "WHERE ST_DWithin(b.geom::geography, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(r)s) "
                "ORDER BY b.geom::geography <-> ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography LIMIT 1",
                {"lat": lat, "lon": lon, "r": BUILDING_LOOKUP_RADIUS_M},
            ).fetchone()
        return int(r["id"]) if r else None

    def observations(self, spot_id: str, start, end, kind: str = "lux"):
        with self.pool.connection() as c:
            return c.execute(
                "SELECT ts, value, flags FROM observations WHERE spot_id = %s AND kind = %s AND ts >= %s AND ts < %s "
                "ORDER BY ts",
                (spot_id, kind, start, end),
            ).fetchall()

    def spots_with_observations(self) -> list[str]:
        with self.pool.connection() as c:
            return [r["spot_id"] for r in c.execute("SELECT DISTINCT spot_id FROM observations").fetchall()]
