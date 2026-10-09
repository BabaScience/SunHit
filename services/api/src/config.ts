import { z } from "zod";

const ConfigSchema = z.object({
  HOST: z.string().default("0.0.0.0"),
  PORT: z.coerce.number().int().default(3000),
  DATABASE_URL: z.string().default("postgres://sunhit:sunhit@localhost:5432/sunhit"),
  REDIS_URL: z.string().default("redis://localhost:6379"),
  MIGRATIONS_DIR: z.string().default("../../db/migrations"),
  // Max time a request waits for the geometry worker (windows, point queries).
  GEOMETRY_TIMEOUT_MS: z.coerce.number().int().default(30_000),
  // Radius used to attach a spot to a building footprint.
  BUILDING_LOOKUP_RADIUS_M: z.coerce.number().default(5),
  LOG_LEVEL: z.string().default("info"),
});

export type Config = z.infer<typeof ConfigSchema>;

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  return ConfigSchema.parse(env);
}
