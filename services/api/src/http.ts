import type { FastifyReply } from "fastify";
import type { ZodType, ZodTypeDef } from "zod";

export class HttpError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly details?: unknown,
  ) {
    super(message);
  }
}

export function parseOr400<T>(schema: ZodType<T, ZodTypeDef, unknown>, data: unknown): T {
  const r = schema.safeParse(data);
  if (!r.success) throw new HttpError(400, "invalid_request", "Request validation failed", r.error.issues);
  return r.data;
}

export function sendError(reply: FastifyReply, err: HttpError) {
  return reply
    .status(err.status)
    .send({ error: err.code, message: err.message, ...(err.details !== undefined ? { details: err.details } : {}) });
}
