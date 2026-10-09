"""Phase 0 gate: predicted vs measured sun-hit start/end times.

For every spot with ground truth, each predicted `sun` segment edge (start or end) is matched to the
nearest observed transition of the same direction. Observed transitions come from the lux step change
(direct sun is several times brighter than shade) or from hand-labelled times (phone timelapse).
Only `sun` segments count; `filtered` ones are ignored. Gate: MAE <= 10 min on modelled spots.

    sunhit-gate --from 2026-10-01 --to 2026-10-14 [--spot sp_x] [--labels labels.csv] [--json out.json]

labels.csv columns: spot_id,date,edge,time   (edge = start|end, time = local HH:MM[:SS])
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pvlib

from sunhit_geo import HorizonProfile, Spot, compute_segments

GATE_MAE_MIN = 10.0


@dataclass
class Edge:
    t: datetime  # UTC
    direction: str  # "start" (shade -> sun) or "end" (sun -> shade)
    cause: str = ""  # what the sun appears from / disappears behind (predicted edges)


@dataclass
class Match:
    spot_id: str
    date: str
    direction: str
    cause: str
    predicted: str
    observed: str | None
    error_min: float | None  # observed - predicted; positive = sun came/left later than predicted


# --- observed transitions ---------------------------------------------------------------------

def detect_transitions(
    ts: pd.Series,
    sun_elevation: pd.Series | None = None,
    resample: str = "30s",
    min_peak_lux: float = 20_000,
    hi_frac: float = 0.5,
    lo_frac: float = 0.3,
    min_dwell: str = "2min",
) -> list[Edge]:
    """Shade/sun transitions from a lux time series (UTC index).

    Hysteresis between lo_frac and hi_frac of the day's 95th percentile; a new state must last
    `min_dwell`. The transition time is where the series crosses the mid threshold.
    """
    s = ts.dropna().sort_index()
    if sun_elevation is not None:  # night-time readings are noise for this purpose
        el = sun_elevation.reindex(s.index, method="nearest")
        s = s[el.to_numpy() > 0]
    if len(s) < 10:
        return []
    s = s.resample(resample).median().dropna()
    peak = float(np.percentile(s.to_numpy(), 95))
    if peak < min_peak_lux:  # overcast day or sensor in permanent shade: no clean step
        return []
    hi, lo, mid = hi_frac * peak, lo_frac * peak, (hi_frac + lo_frac) / 2 * peak

    state = None
    for v in s.to_numpy():
        if v >= hi:
            state = True
            break
        if v <= lo:
            state = False
            break
    if state is None:
        return []

    dwell = pd.Timedelta(min_dwell)
    times, vals = s.index, s.to_numpy()
    edges: list[Edge] = []
    for i, v in enumerate(vals):
        if not ((not state and v >= hi) or (state and v <= lo)):
            continue
        if times[-1] - times[i] < dwell:
            break  # not enough data left to confirm
        # The new state must hold for the dwell time (no fall back across the opposite threshold).
        window = vals[(times >= times[i]) & (times < times[i] + dwell)]
        holds = bool(np.all(window > lo)) if not state else bool(np.all(window < hi))
        if not holds:
            continue
        # Interpolate the crossing of the mid threshold between the previous sample and this one.
        k = max(i - 1, 0)
        v0, v1 = vals[k], v
        frac = 0.0 if v1 == v0 else min(max((mid - v0) / (v1 - v0), 0.0), 1.0)
        t = times[k] + (times[i] - times[k]) * frac
        state = not state
        edges.append(Edge(t.to_pydatetime(), "start" if state else "end"))
    return edges


def match_edges(predicted: list[Edge], observed: list[Edge], max_gap_min: float) -> list[tuple[Edge, Edge | None]]:
    """Greedy nearest match of the same direction within max_gap; each observed edge used once."""
    pairs = []
    for p in predicted:
        for o in observed:
            if o.direction == p.direction:
                gap = abs((o.t - p.t).total_seconds()) / 60
                if gap <= max_gap_min:
                    pairs.append((gap, id(p), id(o), p, o))
    pairs.sort(key=lambda x: x[0])
    used_p, used_o, out = set(), set(), {}
    for _, ip, io, p, o in pairs:
        if ip in used_p or io in used_o:
            continue
        used_p.add(ip)
        used_o.add(io)
        out[ip] = o
    return [(p, out.get(id(p))) for p in predicted]


def predicted_edges(spot: Spot, horizon: HorizonProfile, day: date) -> list[Edge]:
    segs = compute_segments(spot, horizon, day)
    edges = []
    for k, s in enumerate(segs):
        if s.type != "sun":
            continue
        prev = segs[k - 1] if k > 0 else None
        if prev is not None and prev.end == s.start:
            edges.append(Edge(s.start, "start", prev.by or prev.type))
        edges.append(Edge(s.end, "end", s.blocked_after_by or "sunset"))
    return edges


def load_labels(path: Path) -> dict[tuple[str, str], list[Edge]]:
    """Hand-labelled transitions, keyed by (spot_id, date). Times are local to the spot."""
    out: dict[tuple[str, str], list[dict]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.setdefault((row["spot_id"], row["date"]), []).append(row)
    return out  # converted per spot once the timezone is known


def summarize(matches: list[Match]) -> dict:
    errs = np.array([m.error_min for m in matches if m.error_min is not None])
    out = {
        "predicted_edges": len(matches),
        "matched_edges": int(len(errs)),
        "unmatched_edges": len(matches) - int(len(errs)),
    }
    if len(errs):
        out |= {
            "mae_min": round(float(np.mean(np.abs(errs))), 2),
            "median_abs_min": round(float(np.median(np.abs(errs))), 2),
            "p90_abs_min": round(float(np.percentile(np.abs(errs), 90)), 2),
            "bias_min": round(float(np.mean(errs)), 2),
        }
    return out


def run(db, spot_ids: list[str], start: date, end: date, labels: dict, max_gap_min: float) -> list[Match]:
    matches: list[Match] = []
    for spot_id in spot_ids:
        row = db.spot(spot_id)
        stored = db.horizon(spot_id)
        if row is None or stored is None:
            print(f"  {spot_id}: no horizon, skipped", file=sys.stderr)
            continue
        horizon = HorizonProfile.from_dict(stored[0])
        spot = Spot(row.lat, row.lon, row.height_above_ground_m, row.tilt_deg, row.facing_azimuth_deg, row.timezone)
        tz = ZoneInfo(row.timezone)
        d = start
        while d <= end:
            pred = predicted_edges(spot, horizon, d)
            key = (spot_id, d.isoformat())
            if key in labels:
                obs = [
                    Edge(datetime.combine(d, datetime.strptime(r["time"], "%H:%M:%S" if r["time"].count(":") == 2
                                                               else "%H:%M").time(), tz).astimezone(ZoneInfo("UTC")),
                         r["edge"])
                    for r in labels[key]
                ]
            else:
                day_start = datetime(d.year, d.month, d.day, tzinfo=tz)
                rows = db.observations(spot_id, day_start, day_start + timedelta(days=1))
                rows = [r for r in rows if r["flags"] == 0]  # flagged readings are suspect
                if not rows:
                    d += timedelta(days=1)
                    continue
                series = pd.Series([r["value"] for r in rows],
                                   index=pd.DatetimeIndex([r["ts"] for r in rows]).tz_convert("UTC"))
                el = pvlib.solarposition.get_solarposition(series.index, spot.lat, spot.lon)["apparent_elevation"]
                obs = detect_transitions(series, el)
            for p, o in match_edges(pred, obs, max_gap_min):
                matches.append(Match(
                    spot_id, d.isoformat(), p.direction, p.cause,
                    p.t.astimezone(tz).isoformat(timespec="seconds"),
                    o.t.astimezone(tz).isoformat(timespec="seconds") if o else None,
                    round((o.t - p.t).total_seconds() / 60, 2) if o else None,
                ))
            d += timedelta(days=1)
    return matches


def main() -> None:
    from sunhit_horizon.db import Db

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    today = date.today()
    ap.add_argument("--from", dest="start", default=(today - timedelta(days=14)).isoformat())
    ap.add_argument("--to", dest="end", default=(today - timedelta(days=1)).isoformat())
    ap.add_argument("--spot", action="append", help="spot id (repeatable); default: all with observations")
    ap.add_argument("--labels", type=Path, help="CSV of hand-labelled transitions")
    ap.add_argument("--max-gap-min", type=float, default=30.0, help="max predicted/observed gap to count as a match")
    ap.add_argument("--min-edges", type=int, default=10, help="matched edges needed for a verdict")
    ap.add_argument("--json", type=Path, help="write all matches and the summary to this file")
    args = ap.parse_args()

    db = Db()
    labels = load_labels(args.labels) if args.labels else {}
    spot_ids = args.spot or sorted(set(db.spots_with_observations()) | {k[0] for k in labels})
    matches = run(db, spot_ids, date.fromisoformat(args.start), date.fromisoformat(args.end), labels,
                  args.max_gap_min)

    print(f"\nPhase 0 gate  {args.start} .. {args.end}  ({len(spot_ids)} spots)\n")
    print(f"{'spot':<18}{'edges':>7}{'matched':>9}{'MAE':>8}{'median':>8}{'p90':>8}{'bias':>8}")
    for sid in spot_ids:
        s = summarize([m for m in matches if m.spot_id == sid])
        print(f"{sid:<18}{s['predicted_edges']:>7}{s['matched_edges']:>9}"
              f"{s.get('mae_min', float('nan')):>8.1f}{s.get('median_abs_min', float('nan')):>8.1f}"
              f"{s.get('p90_abs_min', float('nan')):>8.1f}{s.get('bias_min', float('nan')):>8.1f}")
    by_cause = {c: summarize([m for m in matches if m.cause == c]) for c in sorted({m.cause for m in matches})}
    print("\nby obstruction:")
    for c, s in by_cause.items():
        print(f"  {c:<10} matched {s['matched_edges']:>4}/{s['predicted_edges']:<4} MAE {s.get('mae_min', float('nan')):.1f} min")
    total = summarize(matches)
    if total["matched_edges"] < args.min_edges:
        verdict = f"NO VERDICT: {total['matched_edges']} matched edges, need {args.min_edges}"
    elif total["mae_min"] <= GATE_MAE_MIN:
        verdict = f"PASS: MAE {total['mae_min']} min <= {GATE_MAE_MIN}"
    else:
        verdict = f"FAIL: MAE {total['mae_min']} min > {GATE_MAE_MIN}"
    print(f"\noverall: {total}\n{verdict}")
    if args.json:
        args.json.write_text(json.dumps({"summary": total, "by_cause": by_cause, "verdict": verdict,
                                         "matches": [asdict(m) for m in matches]}, indent=2))


if __name__ == "__main__":
    main()
