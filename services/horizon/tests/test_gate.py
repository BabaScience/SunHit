from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from sunhit_horizon.gate import Edge, detect_transitions, match_edges, summarize, Match

T0 = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)


def lux_series(profile: list[tuple[int, float]], noise: float = 1500, seed: int = 1) -> pd.Series:
    """profile: (minutes from T0, lux level) breakpoints; 30 s samples with noise."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(T0, T0 + timedelta(minutes=profile[-1][0]), freq="30s")
    minutes = (idx - idx[0]).total_seconds() / 60
    levels = np.zeros(len(idx))
    for (m0, lvl), (m1, _) in zip(profile, profile[1:]):
        levels[(minutes >= m0) & (minutes < m1)] = lvl
    levels[minutes >= profile[-1][0]] = profile[-2][1]
    return pd.Series(levels + rng.normal(0, noise, len(idx)), index=idx)


def test_detects_shade_sun_shade_with_sub_minute_accuracy():
    s = lux_series([(0, 12_000), (60, 85_000), (180, 15_000), (240, 0)])
    edges = detect_transitions(s)
    assert [e.direction for e in edges] == ["start", "end"]
    assert abs((edges[0].t - (T0 + timedelta(minutes=60))).total_seconds()) < 45
    assert abs((edges[1].t - (T0 + timedelta(minutes=180))).total_seconds()) < 45


def test_ignores_a_brief_spike_shorter_than_dwell():
    s = lux_series([(0, 12_000), (60, 90_000), (61, 12_000), (120, 0)])
    assert detect_transitions(s) == []


def test_overcast_day_yields_no_edges():
    s = lux_series([(0, 8_000), (120, 14_000), (240, 0)])
    assert detect_transitions(s) == []


def test_matching_is_one_to_one_same_direction_within_gap():
    p = [Edge(T0, "start"), Edge(T0 + timedelta(hours=2), "end")]
    o = [Edge(T0 + timedelta(minutes=4), "start"), Edge(T0 + timedelta(minutes=5), "end"),
         Edge(T0 + timedelta(hours=3), "end")]
    pairs = match_edges(p, o, max_gap_min=30)
    assert pairs[0][1] is o[0]
    assert pairs[1][1] is None  # the only "end" nearby is 60 min away


def test_summary_stats():
    ms = [Match("sp", "d", "start", "building", "", "", e) for e in (2.0, -4.0, 6.0)] + \
         [Match("sp", "d", "end", "building", "", None, None)]
    s = summarize(ms)
    assert s["matched_edges"] == 3 and s["unmatched_edges"] == 1
    assert s["mae_min"] == 4.0 and s["bias_min"] == round(4 / 3, 2)
