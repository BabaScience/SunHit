import { WindowsSchema } from "@sunhit/contracts";
import type { FastifyInstance } from "fastify";
import { z } from "zod";
import type { AppDeps } from "../app.js";
import { parseOr400 } from "../http.js";
import { timezoneAt } from "../util.js";
import { jobErrorToHttp, resolveDates, WindowsQuery } from "./spots.js";

const PointQuery = WindowsQuery.extend({
  lat: z.coerce.number().min(-90).max(90),
  lon: z.coerce.number().min(-180).max(180),
  height_above_ground_m: z.coerce.number().min(0).max(500).default(1.5),
  tilt_deg: z.coerce.number().min(0).max(90).default(0),
  facing_azimuth_deg: z.coerce.number().min(0).lt(360).default(180),
});

/** Stateless one-off query: no stored spot, no calibration, lower accuracy. */
export async function pointRoutes(app: FastifyInstance, { jobs }: AppDeps) {
  app.get("/v1/point", async (req) => {
    const q = parseOr400(PointQuery, req.query);
    const timezone = timezoneAt(q.lat, q.lon);
    const dates = resolveDates({ date: q.date, from: q.from, to: q.to }, timezone);
    const result = await jobs
      .point({
        lat: q.lat,
        lon: q.lon,
        height_above_ground_m: q.height_above_ground_m,
        tilt_deg: q.tilt_deg,
        facing_azimuth_deg: q.facing_azimuth_deg,
        timezone,
        dates,
      })
      .catch(jobErrorToHttp);
    return WindowsSchema.parse(result);
  });
}
