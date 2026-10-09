import math
from datetime import date

import numpy as np
import pandas as pd
import pvlib
import pytest

from sunhit_geo import RasterLayer, Spot, build_horizon, compute_segments, transmissivity
from sunhit_geo.horizon import OutsideCoverage, flat_horizon
from sunhit_geo.windows import summarize

ROME = dict(lat=41.9028, lon=12.4964, timezone="Europe/Rome")
RES = 1.0
SIZE = 400  # 400 m x 400 m grid centred on (0, 0)


def grid(fill=0.0, dtype=np.float32):
    return np.full((SIZE, SIZE), fill, dtype=dtype)


def layer(a):
    return RasterLayer(a, x0=-SIZE / 2 * RES, y0=SIZE / 2 * RES, res=RES)


def rc(x, y):
    """Row/col slice helpers for a rectangle in map coordinates (metres)."""
    return int(SIZE / 2 - y), int(x + SIZE / 2)


def put_box(arr, x_min, x_max, y_min, y_max, value):
    r0, c0 = rc(x_min, y_max)
    r1, c1 = rc(x_max, y_min)
    arr[r0:r1, c0:c1] = value


def scene():
    return grid(), grid(), grid(0, np.int64), grid()


def horizon_for(ground, bh, bid, can, own_building_id=0, **kw):
    own = layer((bid == own_building_id).astype(np.uint8)) if own_building_id else None
    return build_horizon(0.0, 0.0, ground=layer(ground), building_height=layer(bh),
                         canopy=layer(can), own_footprint=own, near_radius_m=190, **kw)


def test_flat_horizon_sun_window_matches_spa_sunrise_sunset():
    spot = Spot(height_above_ground_m=0, tilt_deg=0, facing_azimuth_deg=180, **ROME)
    day = date(2026, 3, 20)
    segs = compute_segments(spot, flat_horizon(), day)
    # Between the -0.833 deg sunrise convention and the sun centre clearing the 0 deg horizon,
    # the sun is up but below the local horizon: a short blocked-by-terrain segment at each end.
    assert [s.type for s in segs] == ["blocked", "sun", "blocked"]
    assert segs[0].by == segs[2].by == "terrain"
    sun = segs[1]
    times = pd.DatetimeIndex([pd.Timestamp(day, tz="Europe/Rome")])
    ref = pvlib.solarposition.sun_rise_set_transit_spa(times, ROME["lat"], ROME["lon"])
    sunrise = ref["sunrise"].iloc[0].floor("s").to_pydatetime()
    sunset = ref["sunset"].iloc[0].floor("s").to_pydatetime()
    # SPA sunrise is upper-limb-on-horizon; we use the sun centre, about 1.5 min later.
    assert 0 < (sun.start - sunrise).total_seconds() < 150
    assert 0 < (sunset - sun.end).total_seconds() < 150
    assert sun.blocked_after_by == "terrain"
    assert segs[2].blocked_after_by == "sunset"


def test_wall_to_the_south_blocks_winter_noon_not_summer_noon():
    ground, bh, bid, can = scene()
    # 20 m tall building 20-30 m south of the spot, 200 m wide.
    put_box(bh, -100, 100, -30, -20, 20.0)  # not our building: no id needed
    h = horizon_for(ground, bh, bid, can, height_above_ground_m=1.5, facing_azimuth_deg=0)
    south = h.elevation_deg[180]
    # Nudged 1 m north: wall face is 21 m away, top 18.5 m above the eye.
    assert south == pytest.approx(math.degrees(math.atan2(18.5, 21)), abs=1.5)
    assert h.source[180] == 2

    spot = Spot(height_above_ground_m=1.5, tilt_deg=0, facing_azimuth_deg=0, **ROME)
    winter = compute_segments(spot, h, date(2026, 12, 21))
    assert all(s.type == "blocked" for s in winter)
    assert max(winter, key=lambda s: s.minutes).by == "building"
    summer = summarize(compute_segments(spot, h, date(2026, 6, 21)))
    assert summer["expected_sun_minutes"] > 6 * 60


def test_own_building_is_ignored_in_front_but_blocks_behind():
    ground, bh, bid, can = scene()
    # Own building north of the spot: the spot sits on its south facade.
    put_box(bh, -30, 30, 0, 40, 25.0)
    put_box(bid, -30, 30, 0, 40, 11)
    h = horizon_for(ground, bh, bid, can, height_above_ground_m=10,
                    facing_azimuth_deg=180, own_building_id=11)
    assert h.elevation_deg[180] < 1  # open to the south
    assert h.elevation_deg[0] > 80  # own building right behind
    assert h.source[0] == 2


def test_spot_inside_own_footprint_is_released_only_in_front():
    ground, bh, bid, can = scene()
    put_box(bh, -30, 30, -3, 40, 25.0)  # footprint extends past the nudge distance
    put_box(bid, -30, 30, -3, 40, 11)
    h = horizon_for(ground, bh, bid, can, height_above_ground_m=10,
                    facing_azimuth_deg=180, own_building_id=11)
    assert h.elevation_deg[180] < 1
    assert h.elevation_deg[0] > 80


def test_other_wing_of_own_building_still_casts_shade():
    ground, bh, bid, can = scene()
    # U-shape: spot on the inner face of the west wing, facing east; east wing 40 m away.
    put_box(bh, -20, 0, -50, 50, 30.0)
    put_box(bid, -20, 0, -50, 50, 5)
    put_box(bh, 40, 60, -50, 50, 30.0)
    put_box(bid, 40, 60, -50, 50, 5)
    h = horizon_for(ground, bh, bid, can, height_above_ground_m=3,
                    facing_azimuth_deg=90, own_building_id=5)
    assert h.elevation_deg[90] > 30
    assert h.source[90] == 2


def test_vertical_window_facing_north_never_gets_winter_sun():
    spot = Spot(height_above_ground_m=0, tilt_deg=90, facing_azimuth_deg=0, **ROME)
    segs = compute_segments(spot, flat_horizon(), date(2026, 12, 21))
    assert segs and all(s.type == "blocked" for s in segs)
    assert max(segs, key=lambda s: s.minutes).by == "surface"


def test_tree_to_the_east_filters_the_morning_with_seasonal_transmissivity():
    ground, bh, bid, can = scene()
    put_box(can, 15, 25, -40, 40, 18.0)
    h = horizon_for(ground, bh, bid, can, height_above_ground_m=1, facing_azimuth_deg=180)
    assert np.isfinite(h.canopy_elevation_deg[90])
    spot = Spot(height_above_ground_m=1, tilt_deg=0, facing_azimuth_deg=180, **ROME)
    winter = compute_segments(spot, h, date(2026, 1, 15))
    summer = compute_segments(spot, h, date(2026, 7, 15))
    firsts = []
    for segs in (winter, summer):
        lit = [s for s in segs if s.type != "blocked"]
        assert lit[0].type == "filtered" and lit[0].by == "tree"
        assert lit[0].blocked_after_by is None  # filtered ends because the sun clears the tree
        assert lit[1].type == "sun"
        firsts.append(lit[0])
    assert firsts[0].transmissivity > firsts[1].transmissivity


def test_unmapped_building_height_without_footprint_still_blocks():
    ground, bh, bid, can = scene()
    put_box(bh, -10, 10, 10, 20, 30.0)  # height from a raster source, no footprint id
    h = horizon_for(ground, bh, bid, can, height_above_ground_m=1, facing_azimuth_deg=180)
    assert h.elevation_deg[0] > 50 and h.source[0] == 2


def test_far_mountain_with_earth_curvature():
    ground, bh, bid, can = scene()
    far = np.full((400, 400), 0.0, dtype=np.float32)
    far_layer = RasterLayer(far, x0=-20_000, y0=20_000, res=100)
    # 2000 m ridge 10 km east.
    col = int((10_000 + 20_000) / 100)
    far[:, col] = 2000.0
    h = build_horizon(0, 0, 0, 180, layer(ground), layer(bh), layer(can),
                      far_terrain=far_layer, near_radius_m=190, far_radius_m=19_000)
    d = h.distance_m[90]
    drop = d * d * (1 - 0.13) / (2 * 6_371_000)
    assert h.elevation_deg[90] == pytest.approx(math.degrees(math.atan2(2000 - drop, d)), abs=0.05)
    assert h.source[90] == 1


def test_outside_coverage():
    ground, bh, bid, can = scene()
    with pytest.raises(OutsideCoverage):
        build_horizon(10_000, 0, 1, 0, layer(ground), layer(bh), layer(can))


def test_profile_round_trip():
    ground, bh, bid, can = scene()
    put_box(can, 15, 25, -40, 40, 18.0)
    h = horizon_for(ground, bh, bid, can, height_above_ground_m=1, facing_azimuth_deg=180)
    from sunhit_geo import HorizonProfile

    back = HorizonProfile.from_dict(h.to_dict())
    np.testing.assert_allclose(back.elevation_deg, h.elevation_deg, atol=1e-3)
    assert (back.source == h.source).all()


def test_transmissivity_seasons_and_hemispheres():
    assert transmissivity(15, 41.9) > transmissivity(200, 41.9)
    assert transmissivity(200, -33.9) > transmissivity(15, -33.9)


def test_height_source_is_reported_per_azimuth():
    ground, bh, bid, can = scene()
    src = grid(0, np.uint8)
    put_box(bh, -50, 50, 20, 30, 25.0)  # north: surveyed (ctrn = 1)
    put_box(src, -50, 50, 20, 30, 1)
    put_box(bh, -50, 50, -30, -20, 25.0)  # south: default height guess (5)
    put_box(src, -50, 50, -30, -20, 5)
    h = build_horizon(0, 0, 1, 90, layer(ground), layer(bh), layer(can),
                      building_height_source=layer(src), near_radius_m=190)
    d = h.to_dict()
    assert d["height_source"][0] == "ctrn" and d["height_source"][180] == "default"
    assert d["height_source"][90] is None  # open ground to the east: terrain, no building
    assert 0.3 < h.default_height_share < 0.7


def test_profiles_stored_before_height_sources_still_load():
    d = flat_horizon().to_dict()
    del d["height_source"]
    from sunhit_geo import HorizonProfile

    assert HorizonProfile.from_dict(d).height_source.sum() == 0
