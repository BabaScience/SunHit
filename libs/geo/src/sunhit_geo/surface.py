"""Surface model: region grids, the manifest describing built layers, and the object store.

Layout on the object store (bucket `surface`):
    <region>/current.json             -> {"version": "<version>"}
    <region>/<version>/manifest.json
    <region>/<version>/near_ground.tif        terrain, metres above sea level (float32, NaN = no data)
    <region>/<version>/near_building_h.tif    building height above ground, metres (float32, 0 = none)
    <region>/<version>/near_building_src.tif  where each building height came from (uint8 codes,
                                              sunhit_geo.horizon.HEIGHT_SOURCE_NAMES)
    <region>/<version>/near_canopy.tif        canopy height above ground, metres (float32, 0 = none)
    <region>/<version>/far_terrain.tif        coarse terrain for the far horizon (float32)

Needs the `surface` extra (rasterio, pyproj, boto3).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

NEAR_LAYERS = ("near_ground", "near_building_h", "near_building_src", "near_canopy")
FAR_LAYERS = ("far_terrain",)


@dataclass(frozen=True)
class Bounds:
    left: float
    bottom: float
    right: float
    top: float

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x <= self.right and self.bottom <= y <= self.top

    def buffer(self, d: float) -> Bounds:
        return Bounds(self.left - d, self.bottom - d, self.right + d, self.top + d)

    def snap(self, res: float) -> Bounds:
        import math

        return Bounds(
            math.floor(self.left / res) * res,
            math.floor(self.bottom / res) * res,
            math.ceil(self.right / res) * res,
            math.ceil(self.top / res) * res,
        )

    def shape(self, res: float) -> tuple[int, int]:
        return round((self.top - self.bottom) / res), round((self.right - self.left) / res)


@dataclass
class RegionConfig:
    name: str
    center_lat: float
    center_lon: float
    crs: str
    core_half_size_m: float
    near_radius_m: float
    near_res_m: float
    far_radius_m: float
    far_res_m: float
    defaults: dict
    extra_building_rasters: list[dict] = field(default_factory=list)
    building_height_sources: list[dict] = field(default_factory=list)
    description: str = ""

    @classmethod
    def load(cls, path: str | Path) -> RegionConfig:
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    def center_xy(self) -> tuple[float, float]:
        from pyproj import Transformer

        t = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
        return t.transform(self.center_lon, self.center_lat)

    def core_bounds(self) -> Bounds:
        """Where spots are supported."""
        x, y = self.center_xy()
        h = self.core_half_size_m
        return Bounds(x - h, y - h, x + h, y + h).snap(self.near_res_m)

    def near_bounds(self) -> Bounds:
        """Core plus the near ray-casting radius."""
        return self.core_bounds().buffer(self.near_radius_m).snap(self.near_res_m)

    def far_bounds(self) -> Bounds:
        return self.core_bounds().buffer(self.far_radius_m).snap(self.far_res_m)


@dataclass
class Manifest:
    region: str
    version: str
    crs: str
    core_bounds: Bounds
    near_bounds: Bounds
    near_res_m: float
    far_bounds: Bounds
    far_res_m: float
    near_radius_m: float
    far_radius_m: float
    sources: list[dict]
    built_at: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, s: str) -> Manifest:
        d = json.loads(s)
        for k in ("core_bounds", "near_bounds", "far_bounds"):
            d[k] = Bounds(**d[k])
        return cls(**d)


class ObjectStore:
    """S3-compatible store (SeaweedFS in dev). Configured from S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY."""

    def __init__(self, bucket: str = "surface"):
        import boto3

        self.bucket = bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT", "http://localhost:8333"),
            aws_access_key_id=os.environ.get("S3_ACCESS_KEY", "sunhit"),
            aws_secret_access_key=os.environ.get("S3_SECRET_KEY", "sunhit-dev-secret"),
            region_name="us-east-1",
        )

    def ensure_bucket(self) -> None:
        existing = {b["Name"] for b in self.s3.list_buckets().get("Buckets", [])}
        if self.bucket not in existing:
            self.s3.create_bucket(Bucket=self.bucket)

    def put_file(self, key: str, path: Path) -> None:
        self.s3.upload_file(str(path), self.bucket, key)

    def put_text(self, key: str, text: str) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=text.encode("utf-8"))

    def get_text(self, key: str) -> str:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read().decode("utf-8")

    def download(self, key: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        self.s3.download_file(self.bucket, key, str(tmp))
        tmp.replace(path)

    def regions(self) -> list[str]:
        resp = self.s3.list_objects_v2(Bucket=self.bucket, Delimiter="/")
        return [p["Prefix"].rstrip("/") for p in resp.get("CommonPrefixes", [])]

    def current_manifest(self, region: str) -> Manifest:
        version = json.loads(self.get_text(f"{region}/current.json"))["version"]
        return Manifest.from_json(self.get_text(f"{region}/{version}/manifest.json"))
