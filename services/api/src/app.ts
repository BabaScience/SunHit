import Fastify, { type FastifyInstance } from "fastify";
import { collectDefaultMetrics, Histogram, Registry } from "prom-client";
import { ZodError } from "zod";
import type { Config } from "./config.js";
import type { Db } from "./db.js";
import { HttpError, sendError } from "./http.js";
import type { Jobs } from "./jobs.js";
import { observationRoutes } from "./routes/observations.js";
import { pointRoutes } from "./routes/point.js";
import { spotRoutes } from "./routes/spots.js";

export interface AppDeps {
  db: Db;
  jobs: Jobs;
  config: Config;
}

declare module "fastify" {
  interface FastifyRequest {
    /** Accepted from day one so clients never change; enforced from Phase 1. */
    apiKey?: string;
  }
}

export async function buildApp(deps: AppDeps, opts: { logger?: boolean | object } = {}): Promise<FastifyInstance> {
  const app = Fastify({ logger: opts.logger ?? false });

  const registry = new Registry();
  collectDefaultMetrics({ register: registry });
  const httpDuration = new Histogram({
    name: "http_request_duration_seconds",
    help: "HTTP request duration",
    labelNames: ["method", "route", "status"],
    buckets: [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30],
    registers: [registry],
  });

  app.addHook("onRequest", async (req) => {
    const key = req.headers["x-api-key"];
    if (typeof key === "string") req.apiKey = key;
  });
  app.addHook("onResponse", async (req, reply) => {
    httpDuration
      .labels(req.method, req.routeOptions.url ?? "unknown", String(reply.statusCode))
      .observe(reply.elapsedTime / 1000);
  });

  app.setErrorHandler((err, req, reply) => {
    if (err instanceof HttpError) return sendError(reply, err);
    if (err instanceof ZodError) {
      // A response failed its own contract: a bug, not a client error.
      req.log.error({ issues: err.issues }, "response violates contract");
      return sendError(reply, new HttpError(500, "contract_violation", "Response failed contract validation"));
    }
    if ((err as { validation?: unknown }).validation || (err as { statusCode?: number }).statusCode === 400) {
      return sendError(reply, new HttpError(400, "invalid_request", (err as Error).message));
    }
    req.log.error(err);
    return sendError(reply, new HttpError(500, "internal_error", "Internal error"));
  });

  app.get("/health", async (_req, reply) => {
    const checks = { postgres: false, redis: false };
    try {
      await deps.db.query("SELECT 1");
      checks.postgres = true;
    } catch {}
    try {
      checks.redis = await deps.jobs.ping();
    } catch {}
    const ok = checks.postgres && checks.redis;
    return reply.status(ok ? 200 : 503).send({ status: ok ? "ok" : "degraded", checks });
  });

  app.get("/metrics", async (_req, reply) => {
    return reply.header("content-type", registry.contentType).send(await registry.metrics());
  });

  await spotRoutes(app, deps);
  await observationRoutes(app, deps);
  await pointRoutes(app, deps);
  return app;
}
