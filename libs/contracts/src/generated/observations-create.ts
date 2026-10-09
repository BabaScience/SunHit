// Generated from schemas/observations-create.json by scripts/generate.mjs. Do not edit.
import { z } from "zod"

export const ObservationsCreateSchema = z.object({ "readings": z.array(z.object({ "ts_utc": z.string().datetime({ offset: true }), "lux": z.number().optional(), "sun": z.boolean().describe("User tap: sun now / no sun now").optional() }).strict()).min(1).max(500) }).strict().describe("Ground truth for a spot. A single reading or a batch (firmware sends batches after Wi-Fi drops).")
export type ObservationsCreate = z.infer<typeof ObservationsCreateSchema>

