import { resolve } from "node:path";
import { buildApp } from "./app.js";
import { loadConfig } from "./config.js";
import { createDb, migrate } from "./db.js";
import { createBullJobs } from "./jobs.js";

const config = loadConfig();
const db = createDb(config.DATABASE_URL);
const jobs = createBullJobs(config.REDIS_URL, config.GEOMETRY_TIMEOUT_MS);
const app = await buildApp({ db, jobs, config }, { logger: { level: config.LOG_LEVEL } });

await migrate(db, resolve(config.MIGRATIONS_DIR), (msg) => app.log.info(msg));
await app.listen({ host: config.HOST, port: config.PORT });

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.once(signal, async () => {
    await app.close();
    await jobs.close();
    await db.end();
    process.exit(0);
  });
}
