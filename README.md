# Sun-Hit Engine

When will direct sunlight reach this exact spot, and how sure are we? Minute-level sun windows for any
surface, built from open data and proven against real measurements.

- Spec: [docs/Sun-Hit Engine — Technical Spec.docx](docs/Sun-Hit%20Engine%20—%20Technical%20Spec.docx)
- Decisions taken while building: [docs/decisions](docs/decisions)

**Status: Phase 0 — geometry first.** Rome sample tile (10 × 10 km). Sun position (pvlib / NREL SPA),
per-spot 360° horizon from terrain + buildings + trees, `sun` / `filtered` / `blocked` segments, minimal
ground-truth ingest and the Phase 0 gate. No weather, no satellite, no ML yet (`p_sun` is omitted).

## Layout

| Path | What |
|---|---|
| `libs/contracts` | JSON Schemas (source of truth) → generated Zod + TS types |
| `libs/geo` | Python geometry: sun position, horizon ray-casting (Numba), segments, surface manifest |
| `services/api` | Fastify REST API (TypeScript) |
| `services/horizon` | `horizon-builder`: BullMQ worker for horizon builds + geometry queries; the gate CLI |
| `services/dsm-builder` | Fetches open data and fuses the surface model (COGs on the object store, footprints in PostGIS) |
| `services/debug-web` | Debug page (React + MapLibre): place a spot, see its horizon, sun path and segments |
| `firmware/sensor-esp32` | ESP32 + VEML7700 reference sensor |
| `infra/regions` | Region definitions (`rome.json`) |
| `db/migrations` | SQL migrations, applied by the api at start-up |
| `scripts` | `fetch-samples.sh`, `build-surface.sh`, `gate.sh`, `simulate_sensor.py` |

Services never call each other: the api enqueues BullMQ jobs in Redis, the Python worker answers;
data is shared through PostgreSQL (PostGIS + TimescaleDB) and S3-compatible object storage.

## Run it (Docker Desktop, Git Bash or any POSIX shell)

```bash
cp .env.example .env
docker compose up -d postgres redis objectstore
sh scripts/fetch-samples.sh        # ~10 min: TINITALY, GLO-30, canopy, OSM buildings for Rome
sh scripts/build-surface.sh        # ~6 min: fuse at 1 m, publish, load 69k footprints
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

- API: http://127.0.0.1:3000 · debug page: http://127.0.0.1:5173
- Phase 0 has no real auth: everything binds to `127.0.0.1`. Set `API_BIND=<LAN IP>` only on a private
  network so sensors can post.

## API (Phase 0)

| Method | Path | |
|---|---|---|
| POST | `/v1/spots` | Register a spot; starts the horizon build (202) |
| GET | `/v1/spots/{id}` | Spot, including `horizon_status` |
| GET | `/v1/spots/{id}/horizon` | 360° profile with source per azimuth |
| POST | `/v1/spots/{id}/horizon/rebuild` | Rebuild after a surface update |
| GET | `/v1/spots/{id}/windows?date=` or `?from=&to=` | Segments per local day (max 31 days) |
| GET | `/v1/point?lat=&lon=&…` | One-off query, nothing stored |
| POST | `/v1/spots/{id}/devices` | Register a sensor; returns its token once |
| POST | `/v1/spots/{id}/observations` | Lux readings / sun taps (`X-Device-Token`) |
| GET | `/health`, `/metrics` | |

`X-API-Key` is accepted from day one and enforced from Phase 1.

## npm behind a private registry
If your npm points at a private registry (e.g. a corporate Nexus), `npm install` writes that registry's
URLs into `package-lock.json`, and Docker builds then fail (they use the public registry). Keep the
lockfile on public URLs; npm swaps in your registry locally on its own. After any `npm install`, run:

```bash
sed -i -E 's#"resolved": "https://[^"/]+/repository/[^"/]+/#"resolved": "https://registry.npmjs.org/#' package-lock.json
```

## Tests

```bash
npm ci && npm test                                     # contracts + api (vitest)
docker run --rm -v "$PWD/libs/geo:/src" ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
  sh -c "cp -r /src /tmp/g && cd /tmp/g && uv run --extra test pytest -q"
```

## Data and licences
TINITALY 1.1 (CC BY 4.0), Copernicus DEM GLO-30, Meta/WRI canopy height v2 (CC BY 4.0),
OpenStreetMap (ODbL). Attribution is in `data/samples/<region>/raw/SOURCES.md` after fetching.
GlobalBuildingAtlas is **CC BY-NC** and is not used by default (see decisions 0001 §6).
