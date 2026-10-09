// Generated from schemas/spot-create.json by scripts/generate.mjs. Do not edit.
import { z } from "zod"

export const SpotCreateSchema = z.object({ "lat": z.number().gte(-90).lte(90), "lon": z.number().gte(-180).lte(180), "height_above_ground_m": z.number().gte(0).lte(500), "tilt_deg": z.number().gte(0).lte(90).describe("0 = horizontal, 90 = vertical"), "facing_azimuth_deg": z.number().gte(0).lt(360).describe("Clockwise from north, 180 = south"), "label": z.string().max(120).optional() }).strict().describe("Register a surface that can receive sun.")
export type SpotCreate = z.infer<typeof SpotCreateSchema>

