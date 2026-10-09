import { ObservationsCreateSchema } from "@sunhit/contracts";
import type { FastifyInstance } from "fastify";
import { z } from "zod";
import type { AppDeps } from "../app.js";
import { HttpError, parseOr400 } from "../http.js";
import { toRows } from "../observations.js";
import { newId, newToken, sha256 } from "../util.js";

const IdParams = z.object({ id: z.string().regex(/^sp_[a-z0-9]+$/) });
const DeviceCreate = z.object({ label: z.string().max(120).optional() }).strict();

export async function observationRoutes(app: FastifyInstance, { db }: AppDeps) {
  async function assertSpot(id: string) {
    const { rowCount } = await db.query("SELECT 1 FROM spots WHERE id = $1", [id]);
    if (!rowCount) throw new HttpError(404, "not_found", `spot ${id} not found`);
  }

  // Phase 0 convenience: register a sensor for a spot. The token is returned once; only its hash is stored.
  app.post("/v1/spots/:id/devices", async (req, reply) => {
    const { id } = parseOr400(IdParams, req.params);
    const body = parseOr400(DeviceCreate, req.body ?? {});
    await assertSpot(id);
    const deviceId = newId("dev");
    const token = newToken();
    await db.query("INSERT INTO devices (id, spot_id, token_hash, label) VALUES ($1, $2, $3, $4)", [
      deviceId, id, sha256(token), body.label ?? null,
    ]);
    return reply.status(201).send({ device_id: deviceId, spot_id: id, token });
  });

  app.post("/v1/spots/:id/observations", async (req, reply) => {
    const { id } = parseOr400(IdParams, req.params);
    const body = parseOr400(ObservationsCreateSchema, req.body);
    await assertSpot(id);

    // Phase 0 is open on localhost; a device token, when sent, must match this spot.
    let deviceId: string | null = null;
    const token = req.headers["x-device-token"];
    if (typeof token === "string" && token.length > 0) {
      const { rows } = await db.query<{ id: string; spot_id: string }>(
        "UPDATE devices SET last_seen_at = now() WHERE token_hash = $1 RETURNING id, spot_id",
        [sha256(token)],
      );
      const dev = rows[0];
      if (!dev) throw new HttpError(401, "invalid_device_token", "Unknown device token");
      if (dev.spot_id !== id) throw new HttpError(403, "wrong_spot", "This device is registered to another spot");
      deviceId = dev.id;
    }

    const rows = toRows(body.readings);
    if (rows.length === 0) throw new HttpError(400, "invalid_request", "Each reading needs lux or sun");
    // Duplicates (firmware retries) are ignored by the primary key.
    const values: unknown[] = [];
    const tuples = rows.map((r, i) => {
      values.push(id, r.ts, r.kind, r.value, deviceId, r.flags);
      const b = i * 6;
      return `($${b + 1}, $${b + 2}, $${b + 3}, $${b + 4}, $${b + 5}, $${b + 6})`;
    });
    const result = await db.query(
      `INSERT INTO observations (spot_id, ts, kind, value, device_id, flags) VALUES ${tuples.join(", ")}
       ON CONFLICT DO NOTHING`,
      values,
    );
    const flagged = rows.filter((r) => r.flags !== 0).length;
    return reply.status(202).send({ accepted: result.rowCount ?? 0, duplicates: rows.length - (result.rowCount ?? 0), flagged });
  });
}
