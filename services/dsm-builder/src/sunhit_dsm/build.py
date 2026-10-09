"""Fuse terrain + buildings + canopy into the region's surface model and publish it.

Writes Cloud-Optimized GeoTIFFs to the object store (see sunhit_geo.surface for the layout) and
loads building footprints into PostGIS so spots can be attached to their building.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg
import rasterio
import rasterio.shutil
from affine import Affine
from rasterio.warp import Resampling

from sunhit_dsm.sources import (
    Q_DEFAULT,
    Q_RASTER,
    Grid,
    OsmBuildingsSource,
    RasterSource,
    fuse,
    read_osm_buildings,
)
from sunhit_geo.surface import Bounds, Manifest, ObjectStore, RegionConfig


def grid_for(bounds: Bounds, res: float, crs: str) -> Grid:
    return Grid(crs, Affine(res, 0, bounds.left, 0, -res, bounds.top), bounds.shape(res))


def write_cog(path: Path, data: np.ndarray, grid: Grid, nodata: float | None) -> None:
    tmp = path.with_suffix(".tmp.tif")
    with rasterio.open(
        tmp, "w", driver="GTiff", height=grid.shape[0], width=grid.shape[1], count=1, dtype="float32",
        crs=grid.crs, transform=grid.transform, nodata=nodata, tiled=True, compress="deflate", predictor=3,
        BIGTIFF="IF_SAFER",
    ) as dst:
        dst.write(data.astype("float32"), 1)
    rasterio.shutil.copy(tmp, path, driver="COG", compress="deflate", predictor="3", BIGTIFF="IF_SAFER")
    tmp.unlink()


def allowed(src, allow_noncommercial: bool) -> bool:
    if src.commercial_ok:
        return True
    if allow_noncommercial:
        print(f"  WARNING: using non-commercial source {src.name} ({src.license}); evaluation only")
        return True
    print(f"  skipping {src.name}: licence {src.license} is non-commercial "
          f"(set ALLOW_NONCOMMERCIAL_SOURCES=true for evaluation)")
    return False


def build(region: RegionConfig, raw: Path, out_root: Path, allow_noncommercial: bool) -> tuple[Manifest, Path, list]:
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_root / version
    out.mkdir(parents=True, exist_ok=True)
    d = region.defaults
    near = grid_for(region.near_bounds(), region.near_res_m, region.crs)
    far = grid_for(region.far_bounds(), region.far_res_m, region.crs)
    print(f"near grid {near.shape} at {region.near_res_m} m, far grid {far.shape} at {region.far_res_m} m")

    print("terrain (near): TINITALY only; GLO-30 is a surface model and never used near a spot")
    terrain_near = [RasterSource("tinitaly", "terrain", raw / "tinitaly_near.tif", 80, "CC-BY-4.0", True)]
    ground, _ = fuse(terrain_near, near)

    print("terrain (far): TINITALY, GLO-30 where TINITALY has no data")
    terrain_far = [
        RasterSource("tinitaly", "terrain", raw / "tinitaly_far.tif", 80, "CC-BY-4.0", True),
        RasterSource("glo30", "terrain", raw / "glo30_far.tif", 40, "Copernicus-DEM", True),
    ]
    far_terrain, _ = fuse(terrain_far, far)

    print("buildings")
    buildings = list(read_osm_buildings(raw / "osm_buildings.json", d["building_height_m"], d["level_height_m"]))
    print(f"  {len(buildings)} OSM footprints; heights: "
          + ", ".join(f"{k}={sum(1 for b in buildings if b.height_source == k)}"
                      for k in ("tag:height", "tag:levels", "default")))
    building_sources = [OsmBuildingsSource(buildings)]
    for extra in region.extra_building_rasters:
        building_sources.append(RasterSource(
            extra["name"], "buildings", Path(extra["path"]), extra.get("quality", Q_RASTER),
            extra["license"], extra.get("commercial_ok", False), Resampling.nearest, min_value=0.5,
        ))
    building_sources = [s for s in building_sources if allowed(s, allow_noncommercial)]
    building_h, building_q = fuse(building_sources, near, fill=0.0)
    building_h = np.nan_to_num(building_h, nan=0.0)

    print("canopy")
    canopy_src = RasterSource("meta-wri-chm-v2", "canopy", raw / "canopy_near.tif", 50, "CC-BY-4.0", True,
                              Resampling.nearest)
    canopy, _ = fuse([canopy_src], near, fill=0.0)
    canopy = np.nan_to_num(canopy, nan=0.0)
    canopy[canopy < d["min_canopy_height_m"]] = 0.0
    canopy[building_h > 0] = 0.0  # a roof is not a tree

    for name, arr, nodata in [
        ("near_ground", ground, np.nan),
        ("near_building_h", building_h, None),
        ("near_canopy", canopy, None),
    ]:
        print(f"  writing {name}")
        write_cog(out / f"{name}.tif", arr, near, nodata)
    print("  writing far_terrain")
    write_cog(out / "far_terrain.tif", far_terrain, far, np.nan)

    coverage = {
        "ground_valid_pct": round(float(np.isfinite(ground).mean() * 100), 2),
        "building_pixels_pct": round(float((building_h > 0).mean() * 100), 2),
        "building_default_height_pixels_pct": round(float((building_q == Q_DEFAULT).mean() * 100), 2),
        "canopy_pixels_pct": round(float((canopy > 0).mean() * 100), 2),
        "far_terrain_valid_pct": round(float(np.isfinite(far_terrain).mean() * 100), 2),
    }
    print(f"  coverage: {coverage}")
    srcs = terrain_near + terrain_far + building_sources + [canopy_src]
    manifest = Manifest(
        region=region.name,
        version=version,
        crs=region.crs,
        core_bounds=region.core_bounds(),
        near_bounds=region.near_bounds(),
        near_res_m=region.near_res_m,
        far_bounds=region.far_bounds(),
        far_res_m=region.far_res_m,
        near_radius_m=region.near_radius_m,
        far_radius_m=region.far_radius_m,
        sources=[{"name": s.name, "layer": s.layer, "license": s.license, "commercial_ok": s.commercial_ok}
                 for s in srcs] + [{"coverage": coverage}],
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    (out / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    return manifest, out, buildings


def publish(manifest: Manifest, out: Path) -> None:
    store = ObjectStore()
    store.ensure_bucket()
    prefix = f"{manifest.region}/{manifest.version}"
    for f in sorted(out.iterdir()):
        print(f"  upload {prefix}/{f.name}")
        store.put_file(f"{prefix}/{f.name}", f)
    store.put_text(f"{manifest.region}/current.json", f'{{"version": "{manifest.version}"}}')


def load_buildings(manifest: Manifest, buildings: list) -> None:
    url = os.environ.get("DATABASE_URL", "postgres://sunhit:sunhit@localhost:5432/sunhit")
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM buildings WHERE region = %s", (manifest.region,))
        with cur.copy(
            "COPY buildings (id, region, source, source_ref, height_m, height_source, quality, surface_version, geom) "
            "FROM STDIN"
        ) as copy:
            for b in buildings:
                copy.write_row((b.id, manifest.region, "osm", b.source_ref, b.height_m, b.height_source,
                                b.quality, manifest.version, f"SRID=4326;{b.geom.wkt}"))
        cur.execute("ANALYZE buildings")
    print(f"  {len(buildings)} buildings loaded into PostGIS")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--region", default="infra/regions/rome.json")
    ap.add_argument("--data-dir", default="data/samples")
    ap.add_argument("--no-publish", action="store_true", help="build locally only")
    args = ap.parse_args()

    region = RegionConfig.load(args.region)
    base = Path(args.data_dir) / region.name
    allow_nc = os.environ.get("ALLOW_NONCOMMERCIAL_SOURCES", "").lower() == "true"
    manifest, out, buildings = build(region, base / "raw", base / "surface", allow_nc)
    if not args.no_publish:
        print("publishing to object store")
        publish(manifest, out)
        print("loading buildings")
        load_buildings(manifest, buildings)
    # Keep only the latest local build.
    for old in (base / "surface").iterdir():
        if old.is_dir() and old.name != manifest.version:
            shutil.rmtree(old, ignore_errors=True)
    print(f"surface {manifest.region}/{manifest.version} ready")


if __name__ == "__main__":
    tempfile.tempdir = os.environ.get("TMPDIR")
    main()
