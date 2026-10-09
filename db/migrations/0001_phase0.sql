-- Phase 0: spots, horizons, building footprints, ground-truth observations.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Building footprints loaded by dsm-builder. Ids are stable across rebuilds
-- (OSM: way id * 2, relation id * 2 + 1), so spots.building_id stays valid.
CREATE TABLE buildings (
  id              bigint PRIMARY KEY,
  region          text        NOT NULL,
  source          text        NOT NULL,          -- e.g. osm
  source_ref      text,                          -- e.g. way/123456
  height_m        real        NOT NULL,
  height_source   text        NOT NULL,          -- tag:height | tag:levels | raster:<name> | default
  quality         smallint    NOT NULL,          -- higher is better, same scale as the DSM sources
  surface_version text        NOT NULL,
  geom            geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX buildings_geom_idx ON buildings USING gist (geom);
CREATE INDEX buildings_geog_idx ON buildings USING gist ((geom::geography));

CREATE TABLE spots (
  id                    text PRIMARY KEY,
  label                 text,
  lat                   double precision NOT NULL,
  lon                   double precision NOT NULL,
  geom                  geography(Point, 4326) NOT NULL,
  height_above_ground_m real NOT NULL,
  tilt_deg              real NOT NULL,
  facing_azimuth_deg    real NOT NULL,
  timezone              text NOT NULL,
  building_id           bigint,               -- no FK: buildings are reloaded when sources update
  horizon_status        text NOT NULL DEFAULT 'pending'
                        CHECK (horizon_status IN ('pending', 'ready', 'failed', 'outside_coverage')),
  horizon_error         text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX spots_geom_idx ON spots USING gist (geom);

CREATE TABLE horizons (
  spot_id         text PRIMARY KEY REFERENCES spots (id) ON DELETE CASCADE,
  built_at        timestamptz NOT NULL DEFAULT now(),
  surface_version text NOT NULL,
  confidence      text NOT NULL CHECK (confidence IN ('modelled', 'calibrated')),
  profile         jsonb NOT NULL
);

-- Sensors that post ground truth for one spot. Only a SHA-256 hash of the token is stored.
CREATE TABLE devices (
  id           text PRIMARY KEY,
  spot_id      text NOT NULL REFERENCES spots (id) ON DELETE CASCADE,
  token_hash   text NOT NULL UNIQUE,
  label        text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz
);

-- Ground truth. Bad readings are flagged, never dropped (see flags in services/api/src/observations.ts).
CREATE TABLE observations (
  spot_id     text        NOT NULL,
  ts          timestamptz NOT NULL,
  kind        text        NOT NULL CHECK (kind IN ('lux', 'tap')),
  value       double precision NOT NULL,
  device_id   text,
  flags       integer     NOT NULL DEFAULT 0,
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (spot_id, kind, ts)
);
SELECT create_hypertable('observations', by_range('ts', INTERVAL '7 days'));
