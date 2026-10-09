from dataclasses import dataclass


@dataclass(frozen=True)
class Spot:
    """A surface that can receive sun.

    tilt_deg: 0 = horizontal (balcony floor, table), 90 = vertical (window, facade).
    facing_azimuth_deg: direction the surface faces, clockwise from north (180 = south).
    For a horizontal surface it is still used to nudge the spot away from its own building.
    """

    lat: float
    lon: float
    height_above_ground_m: float
    tilt_deg: float
    facing_azimuth_deg: float
    timezone: str = "UTC"
