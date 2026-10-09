// Generated from schemas/horizon.json by scripts/generate.mjs. Do not edit.
import { z } from "zod"

export const HorizonSchema = z.object({ "spot_id": z.string(), "built_at": z.string().datetime({ offset: true }), "surface_version": z.string(), "azimuth_step_deg": z.number(), "elevation_deg": z.array(z.union([z.number(), z.null()])), "source": z.array(z.enum(["none","terrain","building","tree","user"])), "distance_m": z.array(z.union([z.number(), z.null()])), "canopy_elevation_deg": z.array(z.union([z.number(), z.null()])).describe("Top of tree canopy where it rises above the solid horizon, null otherwise"), "segments": z.array(z.object({ "from_azimuth_deg": z.number(), "to_azimuth_deg": z.number(), "source": z.enum(["none","terrain","building","tree","user"]) }).strict()).describe("Runs of azimuth sharing the same obstruction source"), "ground_elevation_m": z.number(), "eye_elevation_m": z.number(), "confidence": z.enum(["modelled","calibrated"]), "notes": z.array(z.string()) }).strict().describe("Per-spot horizon profile: highest blocking angle per azimuth (clockwise from north).")
export type Horizon = z.infer<typeof HorizonSchema>

