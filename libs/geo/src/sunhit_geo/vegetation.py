"""Seasonal light transmissivity through tree canopy (T_veg)."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class VegetationParams:
    # Share of canopy that is deciduous. Rome mixes stone pines and holm oaks (evergreen)
    # with plane trees and limes (deciduous); 0.5 is a placeholder until calibrated.
    deciduous_share: float = 0.5
    t_evergreen: float = 0.10
    t_leaf_on: float = 0.15
    t_leaf_off: float = 0.60
    # Day-of-year transitions for the northern hemisphere: leaves come out between
    # leaf_out_start and leaf_out_end, fall between leaf_fall_start and leaf_fall_end.
    leaf_out_start: int = 90
    leaf_out_end: int = 135
    leaf_fall_start: int = 305
    leaf_fall_end: int = 345


def _leaf_on_fraction(doy: np.ndarray, p: VegetationParams) -> np.ndarray:
    doy = np.asarray(doy, dtype=float)
    out = np.ones_like(doy)
    out = np.where(doy < p.leaf_out_start, 0.0, out)
    ramp_up = (doy - p.leaf_out_start) / (p.leaf_out_end - p.leaf_out_start)
    out = np.where((doy >= p.leaf_out_start) & (doy < p.leaf_out_end), ramp_up, out)
    ramp_down = 1.0 - (doy - p.leaf_fall_start) / (p.leaf_fall_end - p.leaf_fall_start)
    out = np.where((doy >= p.leaf_fall_start) & (doy < p.leaf_fall_end), ramp_down, out)
    out = np.where(doy >= p.leaf_fall_end, 0.0, out)
    return out


def transmissivity(doy, lat: float, params: VegetationParams = VegetationParams()) -> np.ndarray:
    """Fraction of direct light passing through canopy, 0..1, for day(s) of year."""
    doy = np.asarray(doy, dtype=float)
    if lat < 0:  # southern hemisphere: seasons shifted by half a year
        doy = (doy + 182.5) % 365.0
    leaf_on = _leaf_on_fraction(doy, params)
    t_deciduous = params.t_leaf_off + (params.t_leaf_on - params.t_leaf_off) * leaf_on
    s = params.deciduous_share
    return (1.0 - s) * params.t_evergreen + s * t_deciduous
