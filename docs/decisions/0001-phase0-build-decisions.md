# 0001 — Phase 0 build decisions

Date: 2026-10-09 · Status: accepted

Complements the [technical spec](../Sun-Hit%20Engine%20—%20Technical%20Spec.docx), section
"Build decisions for implementation". Where this file and the spec disagree, this file wins.

## Repo location
The monorepo lives at the project root on the Windows filesystem (not inside WSL2).
All Python/GDAL code runs inside Docker containers, so the host OS only needs Docker + Node 22.

## 1. Ground truth from day one (minimal)
- Phase 0 has `POST /v1/spots/{id}/observations` and an `observations` table. No MQTT until Phase 1.
- Hardware: ESP32 + VEML7700 (not BH1750: it saturates around 65 klx, direct sun is about 100 klx).
- Firmware (`firmware/sensor-esp32`) posts `{ts_utc, lux}` every 30 s over HTTP with a static device token
  (`X-Device-Token`); readings are buffered and sent in batches when Wi-Fi drops.
- The gate (`scripts/gate`) detects sun-hit transitions from the lux step change and compares them to the
  predicted `sun` segments. Hand-labelled transitions (phone timelapse fallback) are accepted as CSV.
- Field note: put a small diffuser cap on the sensor and wipe it occasionally; a dirty sensor makes the
  model look worse than it is.

## 2. Terrain
- Near and far terrain come from **TINITALY 1.1** (10 m bare-ground DTM, CC BY 4.0, UTM 32N).
- **Copernicus GLO-30** is only a fallback for the far horizon (2–50 km) where TINITALY has no data
  (sea, outside Italy). It is a surface model, so it is never used within 2 km of a spot.
- Terrain sources are pluggable (the Alps are still open).

## 3. Own building
- Spot creation resolves `building_id` by footprint lookup (nearest footprint within 5 m).
- The horizon is cast from a point nudged 1 m outward along `facing_azimuth`.
- Own-footprint cells are ignored only on the first contiguous run from the origin (the ray "leaving" its
  own building). Once a ray has left the footprint, own cells count again, so other wings of a U-shaped
  building still cast shade, and rays going back into the building are still blocked.
- Known limitation, logged on every horizon: balconies overhead and other self-shading not in the surface
  model. A later user obstruction / AR scan covers it.

## 4. Trees
- Segments have a type: `sun` / `filtered` / `blocked`.
- `filtered` carries `transmissivity` (0–1) and `by: "tree"`. Transmissivity is seasonal
  (deciduous share leafless in winter). No hard-coded threshold: clients decide.
- The Phase 0 gate counts only `sun`.

## 5. Auth
- Phase 0 is open, with ports bound to `127.0.0.1` only.
- The contract accepts an `X-API-Key` header from day one (ignored in Phase 0). Real keys and rate limits
  come in Phase 1. (The spec says `Authorization: Bearer`; Phase 1 will accept both.)

## 6. Building heights — licence problem found while building
The spec lists GlobalBuildingAtlas (GBA) as "Open". **GBA.Height, GBA.Polygon and GBA.LoD1 are
CC BY-NC 4.0 (non-commercial)**, so they cannot be used in a paid product.

Phase 0 therefore uses commercially safe sources by default:
- OpenStreetMap buildings (ODbL): footprints, `height` / `building:levels` tags, otherwise a default height
  (low quality score).
- Any extra building-height raster can be plugged in (`RasterHeightSource`). Non-commercial sources are
  refused unless `ALLOW_NONCOMMERCIAL_SOURCES=true` (evaluation only).

Candidates to decide (open): Overture buildings heights, Copernicus Urban Atlas Building Height 2012
(10 m, Rome covered), Roma Capitale / Regione Lazio CTR, national LiDAR.

## 7. Small contract decisions
- Times in responses are ISO 8601 in the spot's timezone (with offset), not bare `HH:MM`, to stay
  unambiguous across DST changes.
- The windows response returns `segments` (all daytime states) and `geometric_windows` (only `sun`
  segments, as in the spec example). Each segment carries `blocked_after_by`: what ends it.
- The Phase 0 windows computation runs in Python (pvlib) in the `horizon-builder` container. The API
  reaches it only through BullMQ queues, never directly.
- The spec example labels a day-ahead window `source: "nowcast"`; nowcast only covers 0–3 h, so that will
  be `nwp_blend` in Phase 1.
- Projection for the Rome tile: UTM 33N (EPSG:32633).

## 8. Found while building
- **Object storage: SeaweedFS instead of MinIO.** MinIO no longer publishes Docker images (Docker Hub and
  Quay pulls are refused). SeaweedFS 4.47 (Apache-2.0) speaks the same S3 API; only `docker-compose.yml`
  changed. Garage and RustFS are alternatives.
- **Building ids** are stable OSM-derived int64 ids (`way * 2`, `relation * 2 + 1`). No building-id raster:
  the horizon worker rasterizes the spot's own footprint from PostGIS on the fly, so `spots.building_id`
  survives surface rebuilds.
- **Building height coverage in Rome is poor in OSM**: 60,665 of 69,293 footprints (88%) have no
  `height` / `building:levels` tag and use the 15 m default. That is 82% of building area, and it is the
  largest expected source of error in Phase 0. A commercially usable height source is the most valuable
  next data decision (see §6).
- **"Sun hits" means the sun's centre is above the obstruction.** Between the -0.833° sunrise convention and
  the local horizon, the day starts with a short `blocked` segment (`by: terrain`).
- **npm lockfile uses public registry URLs.** The dev machine's npm points at a corporate Nexus with a
  self-signed certificate, which Docker builds cannot reach. `package-lock.json` keeps
  `registry.npmjs.org` URLs; npm swaps in the configured registry locally (`replace-registry-host=npmjs`).
  After `npm install` on that machine, re-normalize with:
  `sed -i -E 's#"resolved": "https://[^"/]+/repository/[^"/]+/#"resolved": "https://registry.npmjs.org/#' package-lock.json`
