"""Layer A + B: classify every minute of a day as sun / filtered / blocked and build segments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pvlib

from sunhit_geo.horizon import SOURCE_BUILDING, SOURCE_TERRAIN, SOURCE_TREE, SOURCE_USER, HorizonProfile
from sunhit_geo.spot import Spot
from sunhit_geo.vegetation import VegetationParams, transmissivity

# State codes, ordered so that a plain array can hold them.
NIGHT, BLOCKED_SURFACE, BLOCKED_TERRAIN, BLOCKED_BUILDING, BLOCKED_USER, FILTERED_TREE, SUN = range(7)
_STATE = {
    BLOCKED_SURFACE: ("blocked", "surface"),
    BLOCKED_TERRAIN: ("blocked", "terrain"),
    BLOCKED_BUILDING: ("blocked", "building"),
    BLOCKED_USER: ("blocked", "user"),
    FILTERED_TREE: ("filtered", "tree"),
    SUN: ("sun", None),
}
_BLOCKED_BY_SOURCE = {
    SOURCE_TERRAIN: BLOCKED_TERRAIN,
    SOURCE_BUILDING: BLOCKED_BUILDING,
    SOURCE_TREE: FILTERED_TREE,
    SOURCE_USER: BLOCKED_USER,
}

# Sun centre below this apparent elevation is night (sunrise/sunset convention: -50 arcmin).
DAYTIME_MIN_ELEVATION_DEG = -0.833
DEFAULT_TEMPERATURE_C = 12.0


@dataclass
class Segment:
    type: str  # "sun" | "filtered" | "blocked"
    start: datetime  # UTC
    end: datetime  # UTC
    by: str | None = None  # what blocks or filters: surface, terrain, building, tree, user
    transmissivity: float | None = None
    blocked_after_by: str | None = None  # what ends the segment; "sunset" at the end of the day

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0

    def to_dict(self, tz: str) -> dict:
        z = ZoneInfo(tz)
        d = {
            "type": self.type,
            "start": self.start.astimezone(z).isoformat(timespec="seconds"),
            "end": self.end.astimezone(z).isoformat(timespec="seconds"),
            "minutes": round(self.minutes, 1),
        }
        if self.by is not None:
            d["by"] = self.by
        if self.transmissivity is not None:
            d["transmissivity"] = round(self.transmissivity, 3)
        if self.blocked_after_by is not None:
            d["blocked_after_by"] = self.blocked_after_by
        return d


def _states(spot: Spot, horizon: HorizonProfile, times: pd.DatetimeIndex) -> np.ndarray:
    altitude = horizon.eye_elevation_m
    sp = pvlib.solarposition.get_solarposition(
        times,
        spot.lat,
        spot.lon,
        altitude=altitude,
        pressure=pvlib.atmosphere.alt2pres(altitude),
        method="nrel_numpy",
        temperature=DEFAULT_TEMPERATURE_C,
    )
    el = sp["apparent_elevation"].to_numpy()
    az = sp["azimuth"].to_numpy()
    aoi = pvlib.irradiance.aoi(
        spot.tilt_deg, spot.facing_azimuth_deg, sp["apparent_zenith"].to_numpy(), az
    )
    h_el, h_src, h_can = horizon.interpolate(az)

    out = np.full(len(times), SUN, dtype=np.int8)
    tree = np.isfinite(h_can) & (el < h_can)
    out[tree] = FILTERED_TREE
    # Sun behind the surface's own plane; an obstruction in front of the sun takes precedence.
    out[aoi >= 90.0] = BLOCKED_SURFACE
    blocked = el <= h_el
    blocked_code = np.array([_BLOCKED_BY_SOURCE.get(int(s), BLOCKED_TERRAIN) for s in h_src], dtype=np.int8)
    out[blocked] = blocked_code[blocked]
    out[el <= DAYTIME_MIN_ELEVATION_DEG] = NIGHT
    return out


def compute_segments(
    spot: Spot,
    horizon: HorizonProfile,
    day: date,
    step_s: int = 60,
    refine_s: int = 10,
    vegetation: VegetationParams = VegetationParams(),
) -> list[Segment]:
    """Daytime segments for one local calendar day in the spot's timezone.

    The day is sampled every `step_s` seconds; each change of state is then refined to `refine_s`.
    """
    tz = ZoneInfo(spot.timezone)
    start_local = datetime(day.year, day.month, day.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    times = pd.date_range(
        start_local.astimezone(ZoneInfo("UTC")),
        end_local.astimezone(ZoneInfo("UTC")),
        freq=f"{step_s}s",
        inclusive="left",
    )
    codes = _states(spot, horizon, times)

    # Transition boundaries, refined between the two coarse samples that disagree.
    change = np.flatnonzero(codes[1:] != codes[:-1])
    boundaries: list[pd.Timestamp] = []
    if len(change):
        sub = np.arange(refine_s, step_s, refine_s)
        fine_times = pd.DatetimeIndex(
            [times[i] + pd.Timedelta(seconds=int(s)) for i in change for s in sub]
        )
        fine = _states(spot, horizon, fine_times).reshape(len(change), len(sub)) if len(sub) else None
        for n, i in enumerate(change):
            t = times[i + 1]
            if fine is not None:
                differs = np.flatnonzero(fine[n] != codes[i])
                if len(differs):
                    t = times[i] + pd.Timedelta(seconds=int(sub[differs[0]]))
            boundaries.append(t)

    edges = [times[0]] + boundaries + [pd.Timestamp(end_local.astimezone(ZoneInfo("UTC")))]
    run_codes = [codes[0]] + [codes[i + 1] for i in change]

    t_veg = float(transmissivity(day.timetuple().tm_yday, spot.lat, vegetation))
    segments: list[Segment] = []
    for k, code in enumerate(run_codes):
        if code == NIGHT:
            continue
        kind, by = _STATE[int(code)]
        segments.append(
            Segment(
                type=kind,
                start=edges[k].to_pydatetime(),
                end=edges[k + 1].to_pydatetime(),
                by=by,
                transmissivity=t_veg if kind == "filtered" else None,
            )
        )

    for k, seg in enumerate(segments):
        nxt = segments[k + 1] if k + 1 < len(segments) else None
        if nxt is not None and nxt.start == seg.end:
            seg.blocked_after_by = nxt.by if nxt.by is not None else None
        else:
            seg.blocked_after_by = "sunset"
    return segments


def summarize(segments: list[Segment]) -> dict:
    return {
        "expected_sun_minutes": round(sum(s.minutes for s in segments if s.type == "sun"), 1),
        "filtered_minutes": round(sum(s.minutes for s in segments if s.type == "filtered"), 1),
    }
