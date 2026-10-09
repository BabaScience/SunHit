// Generated from schemas/error.json by scripts/generate.mjs. Do not edit.
import { z } from "zod"

export const ApiErrorSchema = z.object({ "error": z.string(), "message": z.string(), "details": z.any().optional() })
export type ApiError = z.infer<typeof ApiErrorSchema>

