"""horizon-builder: BullMQ worker for horizon builds and geometry queries.

Queues (shared with services/api/src/jobs.ts):
  horizon-build   {spot_id}                    ray-cast and store the spot's horizon
  geometry        windows {spot_id, dates}     sun segments per day from the stored horizon
                  point   {lat, lon, ...}      one-off horizon + segments, nothing stored
Exposes /health and /metrics on METRICS_PORT.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bullmq import Worker
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from sunhit_horizon.db import Db
from sunhit_horizon.service import GeometryService, JobFailure
from sunhit_horizon.surface_cache import SurfaceCache

log = logging.getLogger("horizon-builder")

JOBS = Counter("sunhit_jobs_total", "Jobs processed", ["queue", "name", "outcome"])
DURATION = Histogram("sunhit_job_duration_seconds", "Job duration", ["queue", "name"],
                     buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 60))


class _Http(BaseHTTPRequestHandler):
    db: Db

    def do_GET(self):  # noqa: N802
        if self.path == "/metrics":
            body, ctype, status = generate_latest(), CONTENT_TYPE_LATEST, 200
        elif self.path == "/health":
            try:
                self.db.ping()
                body, status = b'{"status":"ok"}', 200
            except Exception:
                body, status = b'{"status":"degraded"}', 503
            ctype = "application/json"
        else:
            body, ctype, status = b"not found", "text/plain", 404
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # quiet
        pass


async def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("botocore", "boto3", "s3transfer", "urllib3", "redis", "rasterio", "numba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    db = Db()
    surface = SurfaceCache(Path(os.environ.get("CACHE_DIR", "/cache")))
    await asyncio.to_thread(surface.refresh, True)
    if not surface.regions:
        log.warning("no surface model published yet: run dsm-builder (see README)")
    service = GeometryService(db, surface)
    await asyncio.to_thread(warm_up)

    _Http.db = db
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("METRICS_PORT", "9100"))), _Http)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def timed(queue: str, name: str, fn, *args):
        t0 = time.perf_counter()
        try:
            result = fn(*args)
            JOBS.labels(queue, name, "ok").inc()
            return result
        except Exception:
            JOBS.labels(queue, name, "error").inc()
            raise
        finally:
            DURATION.labels(queue, name).observe(time.perf_counter() - t0)

    async def horizon_job(job, _token):
        spot_id = job.data["spot_id"]
        try:
            result = await asyncio.to_thread(timed, "horizon-build", "build", service.build_spot_horizon, spot_id)
            log.info("horizon %s: %s", spot_id, result["status"])
            return result
        except Exception as e:
            attempts = (job.opts or {}).get("attempts", 1)
            if job.attemptsMade + 1 >= attempts and not isinstance(e, JobFailure):
                db.set_status(spot_id, "failed", str(e)[:500])
            log.exception("horizon %s failed", spot_id)
            raise

    async def geometry_job(job, _token):
        if job.name == "windows":
            return await asyncio.to_thread(
                timed, "geometry", "windows", service.windows, job.data["spot_id"], job.data["dates"]
            )
        if job.name == "point":
            return await asyncio.to_thread(timed, "geometry", "point", service.point, job.data)
        raise JobFailure("unknown_job", job.name)

    concurrency = int(os.environ.get("WORKER_CONCURRENCY", "2"))
    workers = [
        Worker("horizon-build", horizon_job, {"connection": redis_url, "concurrency": concurrency}),
        Worker("geometry", geometry_job, {"connection": redis_url, "concurrency": concurrency}),
    ]
    log.info("horizon-builder ready (regions: %s)", ", ".join(surface.regions) or "none")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    log.info("shutting down")
    await asyncio.gather(*(w.close() for w in workers))
    server.shutdown()


def warm_up() -> None:
    """First pvlib/numba call is slow (imports, JIT); pay it at boot, not on a user request."""
    from datetime import date

    import numpy as np

    from sunhit_geo import RasterLayer, Spot, build_horizon, compute_segments
    from sunhit_geo.horizon import flat_horizon

    compute_segments(Spot(41.9, 12.5, 1, 0, 180, "Europe/Rome"), flat_horizon(), date(2026, 6, 21))
    z = np.zeros((8, 8), np.float32)
    layer = lambda a: RasterLayer(a, 0, 8, 1)  # noqa: E731
    build_horizon(4, 4, 1, 0, layer(z), layer(z), layer(z), far_terrain=layer(z),
                  own_footprint=layer(np.ones((8, 8), np.uint8)))


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
