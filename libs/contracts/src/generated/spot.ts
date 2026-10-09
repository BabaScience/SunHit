// Generated from schemas/spot.json by scripts/generate.mjs. Do not edit.
import { z } from "zod"

export const SpotSchema = z.object({ "id": z.string().regex(new RegExp("^sp_[a-z0-9]+$")), "label": z.union([z.string(), z.null()]).optional(), "lat": z.number(), "lon": z.number(), "height_above_ground_m": z.number(), "tilt_deg": z.number(), "facing_azimuth_deg": z.number(), "timezone": z.string(), "building_id": z.union([z.number().int().describe("Footprint the spot belongs to, resolved at creation"), z.null().describe("Footprint the spot belongs to, resolved at creation")]).describe("Footprint the spot belongs to, resolved at creation"), "horizon_status": z.enum(["pending","ready","failed","outside_coverage"]), "horizon_error": z.union([z.string(), z.null()]).optional(), "horizon_confidence": z.enum(["modelled","calibrated"]), "created_at": z.string().datetime({ offset: true }) }).strict()
export type Spot = z.infer<typeof SpotSchema>

