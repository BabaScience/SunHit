# 0002 — Building heights: stacked sources

Date: 2026-10-10 · Status: accepted · Supersedes the GlobalBuildingAtlas line in the spec's data sources.

## Decision
Building heights are fused per 1 m pixel, highest priority first. A source is used only where the ones
above it have no value:

| # | Source | Licence | Code |
|---|---|---|---|
| 1 | User calibration / sensors (later) | own data | `user` |
| 2 | Regional technical map — Lazio CTRN 2014 1:5,000 (DBGT `UN_VOL`, `UN_VOL_AV`) | CC BY 4.0 | `ctrn` |
| 3 | OSM `height` | ODbL | `osm_height` |
| 4 | OSM `building:levels` × level height (3.5 m in Rome's config) | ODbL | `osm_levels` |
| 5 | Urban Atlas Building Height 2012, 10 m — only inside known footprints | EEA re-use (commercial OK, attribute) | `urban_atlas` |
| 6 | Default height (15 m) | — | `default` |

National LiDAR is parked until we expand beyond Lazio. Overture is skipped for Rome: its heights are
largely OSM, so it would repeat the same gap.

## Rules found while building
- **Surveyed geometry wins too.** Where an OSM footprint overlaps any CTRN volume, the OSM pixels that CTRN
  does not cover are dropped. Without this, outline misalignment and courtyards drawn filled in OSM left
  2.9 M pixels (about 2.9 km²) of false 15 m buildings in the Rome tile. Footprints with no CTRN volume
  (built after the survey) keep OSM.
- **Urban Atlas only replaces the default inside footprints.** A 10 m block pixel straddles streets, so it
  never creates buildings.
- **CTRN volumes** split buildings into parts of different heights; where volumes overlap (elevated parts,
  `LIVELLO = 1`), the tallest wins. About 80% of Rome's volumes are dated 2003, the rest 2014.
- **CRS:** CTRN is ETRS89 / UTM 33N (EPSG:25833); the model grid is WGS84 / UTM 33N. The 2026 offset is about
  0.7 m and is ignored for now.
- **Fetching:** sheets are 100–580 MB zips on the Lazio geoportal; only `UN_VOL` is read out of them with
  HTTP range requests (57 MB read instead of 960 MB for Rome). Sheets are selected by the shapefile
  header bounding box.

## API
- `GET /v1/spots/{id}/horizon` returns `height_source` per azimuth and per run, `default_height_share`,
  and `height_confidence` (`low` when more than 20% of building-obstructed azimuths use the default).
  `default` runs are the calibration priority list.
- `buildings.height_source` in PostGIS holds the source of each footprint's tallest fused pixel.

## Effect on the Rome tile
| | Before | After |
|---|---|---|
| Building pixels with default height | 82% | 0.99% |
| Building pixels from CTRN | — | 98.3% |
| Via del Corso test balcony, direct sun on Oct 10 | 205 min | 105 min (opposite facades about 21 m, not 15 m) |

## Scaling beyond Lazio
Most Italian regions publish their technical maps openly (IODL 2.0, CC BY or ODbL). Each region needs one
adapter in `services/dsm-builder/src/sunhit_dsm/heights.py` that yields polygons with a height above ground;
`CtrnVolumesSource` already handles the national DBGT schema.
