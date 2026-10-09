import { Queue, QueueEvents } from "bullmq";

/** Queue names shared with the Python workers (services/horizon). */
export const HORIZON_QUEUE = "horizon-build";
export const GEOMETRY_QUEUE = "geometry";

export interface WindowsJob {
  spot_id: string;
  dates: string[];
}

export interface PointJob {
  lat: number;
  lon: number;
  height_above_ground_m: number;
  tilt_deg: number;
  facing_azimuth_deg: number;
  timezone: string;
  dates: string[];
}

/** Worker-side failure that maps to an HTTP error (e.g. outside_coverage). */
export class JobError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export interface Jobs {
  buildHorizon(spotId: string): Promise<void>;
  windows(job: WindowsJob): Promise<unknown>;
  point(job: PointJob): Promise<unknown>;
  ping(): Promise<boolean>;
  close(): Promise<void>;
}

function parseRedisUrl(url: string) {
  const u = new URL(url);
  return { host: u.hostname, port: Number(u.port || 6379), password: u.password || undefined };
}

/** Python workers fail jobs with "<code>: <message>"; known codes become JobErrors. */
function toJobError(err: unknown): Error {
  const msg = err instanceof Error ? err.message : String(err);
  const m = /^([a-z_]+): (.*)$/s.exec(msg);
  if (m) return new JobError(m[1]!, m[2]!);
  return err instanceof Error ? err : new Error(msg);
}

export function createBullJobs(redisUrl: string, timeoutMs: number): Jobs {
  const connection = parseRedisUrl(redisUrl);
  const horizon = new Queue(HORIZON_QUEUE, { connection });
  const geometry = new Queue(GEOMETRY_QUEUE, { connection });
  const geometryEvents = new QueueEvents(GEOMETRY_QUEUE, { connection });

  async function call(name: string, data: object): Promise<unknown> {
    const job = await geometry.add(name, data, {
      removeOnComplete: { age: 600 },
      removeOnFail: { age: 3600 },
    });
    try {
      const result = await job.waitUntilFinished(geometryEvents, timeoutMs);
      return typeof result === "string" ? JSON.parse(result) : result;
    } catch (err) {
      throw toJobError(err);
    }
  }

  return {
    async buildHorizon(spotId) {
      await horizon.add(
        "build",
        { spot_id: spotId },
        { jobId: `horizon-${spotId}-${Date.now()}`, attempts: 3, backoff: { type: "exponential", delay: 2000 } },
      );
    },
    windows: (job) => call("windows", job),
    point: (job) => call("point", job),
    async ping() {
      await horizon.getJobCounts("waiting");
      return true;
    },
    async close() {
      await Promise.all([horizon.close(), geometry.close(), geometryEvents.close()]);
    },
  };
}
