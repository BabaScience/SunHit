"""Post synthetic lux readings for a spot, shaped like its predicted sun windows.

Tests the ground-truth loop (device token -> observations -> gate) without hardware. It says nothing
about model accuracy: the "measurements" are derived from the model itself, shifted by --shift-min.

    python scripts/simulate_sensor.py --spot sp_x --token dt_x --from 2026-10-03 --to 2026-10-08 --shift-min 3
"""

import argparse
import json
import random
import urllib.request
from datetime import date, datetime, timedelta, timezone

SHADE_LUX, SUN_LUX = 12_000.0, 85_000.0


def call(url: str, body: dict | None = None, token: str | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"content-type": "application/json"} | ({"x-device-token": token} if token else {}),
                                 method="POST" if body else "GET")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://127.0.0.1:3000")
    ap.add_argument("--spot", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--shift-min", type=float, default=3.0, help="sensor sees each transition this much later")
    ap.add_argument("--cloud", action="store_true", help="add one 20-minute cloud during the first sun window")
    args = ap.parse_args()
    rng = random.Random(42)

    d, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    while d <= end:
        w = call(f"{args.api}/v1/spots/{args.spot}/windows?date={d}")["days"][0]
        shift = timedelta(minutes=args.shift_min)
        suns = [(datetime.fromisoformat(s["start"]) + shift, datetime.fromisoformat(s["end"]) + shift)
                for s in w["geometric_windows"]]
        clouds = []
        if args.cloud and suns:
            c0 = suns[0][0] + (suns[0][1] - suns[0][0]) / 2
            clouds.append((c0, c0 + timedelta(minutes=20)))
        segs = w["segments"]
        t = datetime.fromisoformat(segs[0]["start"]).astimezone(timezone.utc)
        stop = datetime.fromisoformat(segs[-1]["end"]).astimezone(timezone.utc)
        readings = []
        while t < stop:
            in_sun = any(a <= t < b for a, b in suns) and not any(a <= t < b for a, b in clouds)
            lux = (SUN_LUX if in_sun else SHADE_LUX) * rng.uniform(0.93, 1.07)
            readings.append({"ts_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "lux": round(lux, 1)})
            t += timedelta(seconds=30)
        sent = 0
        for i in range(0, len(readings), 500):
            r = call(f"{args.api}/v1/spots/{args.spot}/observations", {"readings": readings[i:i + 500]}, args.token)
            sent += r["accepted"]
        print(f"{d}: {sent} readings, {len(suns)} sun windows")
        d += timedelta(days=1)


if __name__ == "__main__":
    main()
