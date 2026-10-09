import { HorizonSchema, SpotCreateSchema, SpotSchema, WindowsSchema, type Spot } from "@sunhit/contracts";
import type { FastifyInstance } from "fastify";
import { z } from "zod";
import type { AppDeps } from "../app.js";
import { HttpError, parseOr400 } from "../http.js";
import { JobError } from "../jobs.js";
import { dateRange, newId, sourceSegments, timezoneAt, todayIn } from "../util.js";

export const MAX_WINDOW_DAYS = 31;

interface SpotRow {
  id: string;
  label: string | null;
  lat: number;
  lon: number;
  height_above_ground_m: number;
  tilt_deg: number;
  facing_azimuth_deg: number;
  timezone: string;
  building_id: string | null; // bigint comes back as a string
  horizon_status: Spot["horizon_status"];
  horizon_error: string | null;
  horizon_confidence: Spot["horizon_confidence"] | null;
  created_at: Date;
}

const SPOT_COLUMNS = `s.id, s.label, s.lat, s.lon, s.height_above_ground_m, s.tilt_deg, s.facing_azimuth_deg,
  s.timezone, s.building_id, s.horizon_status, s.horizon_error, h.confidence AS horizon_confidence, s.created_at`;

export function toSpot(r: SpotRow): Spot {
  return SpotSchema.parse({
    id: r.id,
    label: r.label,
    lat: r.lat,
    lon: r.lon,
    height_above_ground_m: r.height_above_ground_m,
    tilt_deg: r.tilt_deg,
    facing_azimuth_deg: r.facing_azimuth_deg,
    timezone: r.timezone,
    building_id: r.building_id === null ? null : Number(r.building_id),
    horizon_status: r.horizon_status,
    horizon_error: r.horizon_error,
    horizon_confidence: r.horizon_confidence ?? "modelled",
    created_at: r.created_at.toISOString(),
  });
}

const IdParams = z.object({ id: z.string().regex(/^sp_[a-z0-9]+$/) });

export const WindowsQuery = z
  .object({
    date: z.string().optional(),
    from: z.string().optional(),
    to: z.string().optional(),
  })
  .strict();

/** Resolves ?date= or ?from=&to= (default: today in the spot's timezone). */
export function resolveDates(q: z.infer<typeof WindowsQuery>, tz: string): string[] {
  try {
    if (q.date) return dateRange(q.date, q.date, 1);
    if (q.from) return dateRange(q.from, q.to ?? q.from, MAX_WINDOW_DAYS);
    const today = todayIn(tz);
    return [today];
  } catch (err) {
    throw new HttpError(400, "invalid_request", (err as Error).message);
  }
}

export function jobErrorToHttp(err: unknown): never {
  if (err instanceof JobError) {
    if (err.code === "outside_coverage") throw new HttpError(422, err.code, err.message);
    if (err.code === "not_ready") throw new HttpError(409, "horizon_not_ready", err.message);
    if (err.code === "not_found") throw new HttpError(404, err.code, err.message);
  }
  if (err instanceof Error && /timed out|timeout/i.test(err.message)) {
    throw new HttpError(504, "geometry_timeout", "The geometry worker did not answer in time");
  }
  throw err;
}

export async function spotRoutes(app: FastifyInstance, deps: AppDeps) {
  const { db, jobs, config } = deps;

  async function loadSpot(id: string): Promise<SpotRow> {
    const { rows } = await db.query<SpotRow>(
      `SELECT ${SPOT_COLUMNS} FROM spots s LEFT JOIN horizons h ON h.spot_id = s.id WHERE s.id = $1`,
      [id],
    );
    if (!rows[0]) throw new HttpError(404, "not_found", `spot ${id} not found`);
    return rows[0];
  }

  app.post("/v1/spots", async (req, reply) => {
    const body = parseOr400(SpotCreateSchema, req.body);
    const id = newId("sp");
    const tz = timezoneAt(body.lat, body.lon);
    // Attach to the nearest footprint within a few metres: balconies usually sit just outside it.
    await db.query(
      `INSERT INTO spots (id, label, lat, lon, geom, height_above_ground_m, tilt_deg, facing_azimuth_deg, timezone, building_id)
       VALUES ($1, $2, $3, $4, ST_SetSRID(ST_MakePoint($4, $3), 4326)::geography, $5, $6, $7, $8,
         (SELECT b.id FROM buildings b
           WHERE ST_DWithin(b.geom::geography, ST_SetSRID(ST_MakePoint($4, $3), 4326)::geography, $9)
           ORDER BY b.geom::geography <-> ST_SetSRID(ST_MakePoint($4, $3), 4326)::geography
           LIMIT 1))`,
      [id, body.label ?? null, body.lat, body.lon, body.height_above_ground_m, body.tilt_deg,
        body.facing_azimuth_deg, tz, config.BUILDING_LOOKUP_RADIUS_M],
    );
    await jobs.buildHorizon(id);
    const spot = toSpot(await loadSpot(id));
    return reply.status(202).header("location", `/v1/spots/${id}`).send(spot);
  });

  app.get("/v1/spots/:id", async (req) => {
    const { id } = parseOr400(IdParams, req.params);
    return toSpot(await loadSpot(id));
  });

  app.get("/v1/spots/:id/horizon", async (req) => {
    const { id } = parseOr400(IdParams, req.params);
    const spot = await loadSpot(id);
    const { rows } = await db.query<{ built_at: Date; surface_version: string; confidence: string; profile: any }>(
      "SELECT built_at, surface_version, confidence, profile FROM horizons WHERE spot_id = $1",
      [id],
    );
    const h = rows[0];
    if (!h) {
      throw new HttpError(409, "horizon_not_ready", `horizon is ${spot.horizon_status}`, {
        horizon_status: spot.horizon_status,
        horizon_error: spot.horizon_error,
      });
    }
    const p = h.profile;
    return HorizonSchema.parse({
      spot_id: id,
      built_at: h.built_at.toISOString(),
      surface_version: h.surface_version,
      azimuth_step_deg: p.azimuth_step_deg,
      elevation_deg: p.elevation_deg,
      source: p.source,
      distance_m: p.distance_m,
      canopy_elevation_deg: p.canopy_elevation_deg,
      segments: sourceSegments(p.source, p.azimuth_step_deg),
      ground_elevation_m: p.ground_elevation_m,
      eye_elevation_m: p.eye_elevation_m,
      confidence: h.confidence,
      notes: p.notes ?? [],
    });
  });

  app.get("/v1/spots/:id/windows", async (req) => {
    const { id } = parseOr400(IdParams, req.params);
    const q = parseOr400(WindowsQuery, req.query);
    const spot = await loadSpot(id);
    if (spot.horizon_status !== "ready") {
      throw new HttpError(409, "horizon_not_ready", `horizon is ${spot.horizon_status}`, {
        horizon_status: spot.horizon_status,
        horizon_error: spot.horizon_error,
      });
    }
    const dates = resolveDates(q, spot.timezone);
    const result = await jobs.windows({ spot_id: id, dates }).catch(jobErrorToHttp);
    return WindowsSchema.parse(result);
  });

  app.post("/v1/spots/:id/horizon/rebuild", async (req, reply) => {
    const { id } = parseOr400(IdParams, req.params);
    await loadSpot(id);
    await db.query("UPDATE spots SET horizon_status = 'pending', horizon_error = NULL, updated_at = now() WHERE id = $1", [id]);
    await jobs.buildHorizon(id);
    return reply.status(202).send(toSpot(await loadSpot(id)));
  });
}
