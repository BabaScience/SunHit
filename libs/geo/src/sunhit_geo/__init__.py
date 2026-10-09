"""Shared Sun-Hit geometry.

Layer A (geometry) and Layer B (obstruction) of the prediction model:
P_sun = G * O * T_veg * P_clear. This package covers G, O and T_veg; P_clear arrives in Phase 1.
"""

from sunhit_geo.horizon import HorizonProfile, RasterLayer, SOURCE_NAMES, build_horizon
from sunhit_geo.spot import Spot
from sunhit_geo.vegetation import VegetationParams, transmissivity
from sunhit_geo.windows import Segment, compute_segments

GEOMETRY_MODEL_VERSION = "geometry-0.1.0"

__all__ = [
    "GEOMETRY_MODEL_VERSION",
    "HorizonProfile",
    "RasterLayer",
    "SOURCE_NAMES",
    "Segment",
    "Spot",
    "VegetationParams",
    "build_horizon",
    "compute_segments",
    "transmissivity",
]
