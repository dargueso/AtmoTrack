"""Horizontal grid description and longitude/latitude conventions.

The trackers work on 2-D ``lon``/``lat`` arrays of shape ``(ny, nx)``.  This
module describes such a grid (regular lat/lon or curvilinear, any latitude
order, any longitude convention) so that the rest of the code does not have
to assume the ERA5 layout (latitude descending, longitude in −180..180).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np

from atmotrack.constants import const


def haversine(lat1, lon1, lat2, lon2):
    """Function to calculate grid distances lat-lon
    This uses the Haversine formula
    lat,lon : input coordinates (degrees) - array or float
    dist_m : distance (m)
    https://en.wikipedia.org/wiki/Haversine_formula
    """
    # convert decimal degrees to radians
    lon1 = np.radians(lon1)
    lon2 = np.radians(lon2)
    lat1 = np.radians(lat1)
    lat2 = np.radians(lat2)

    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    c = 2 * np.arcsin(np.sqrt(a))
    # Radius of earth in kilometers is 6371
    dist_m = c * const.earth_radius
    return dist_m


def calc_grid_distance_area(lat, lon):
    """Function to calculate grid parameters
    It uses haversine function to approximate distances
    It approximates the first row and column to the sencond
    because coordinates of grid cell center are assumed
    lat, lon: input coordinates(degrees) 2D [y,x] dimensions
    dx: distance (m)
    dy: distance (m)
    area: area of grid cell (m2)
    grid_distance: average grid distance over the domain (m)
    """
    dy = np.zeros(lat.shape)
    dx = np.zeros(lon.shape)

    dx[:, 1:] = haversine(lat[:, 1:], lon[:, 1:], lat[:, :-1], lon[:, :-1])
    dy[1:, :] = haversine(lat[1:, :], lon[1:, :], lat[:-1, :], lon[:-1, :])

    dx[:, 0] = dx[:, 1]
    dy[0, :] = dy[1, :]

    area = dx * dy
    grid_distance = np.mean(np.append(dy[:, :, None], dx[:, :, None], axis=2))

    return dx, dy, area, grid_distance


PM180 = "-180..180"
P360 = "0..360"
MIXED = "mixed"


def to_pm180(lon):
    """Wrap longitudes into −180..180 (array or scalar)."""
    return ((np.asarray(lon, dtype=float) + 180.0) % 360.0) - 180.0


def to_360(lon):
    """Wrap longitudes into 0..360 (array or scalar)."""
    return np.asarray(lon, dtype=float) % 360.0


def lon_convention(lon) -> str:
    """``"-180..180"``, ``"0..360"`` or ``"mixed"`` for the given longitudes."""
    lon = np.asarray(lon, dtype=float)
    if lon.max() <= 180.0 + 1e-6 and lon.min() >= -180.0 - 1e-6:
        return PM180 if lon.min() < 0 or lon.max() <= 180.0 else P360
    if lon.min() >= -1e-6 and lon.max() <= 360.0 + 1e-6:
        return P360
    return MIXED


def is_regular(lon2d, lat2d, tol: float = 1e-4) -> bool:
    """True when latitude is constant along rows and longitude along columns."""
    lon2d = np.asarray(lon2d)
    lat2d = np.asarray(lat2d)
    if lon2d.ndim != 2 or lat2d.ndim != 2:
        return False
    lat_ok = np.allclose(lat2d, lat2d[:, :1], atol=tol)
    lon_ok = np.allclose(lon2d, lon2d[:1, :], atol=tol)
    return bool(lat_ok and lon_ok)


def lat_direction(lat2d) -> int:
    """+1 when latitude increases with the row index, −1 when it decreases."""
    lat2d = np.asarray(lat2d)
    if lat2d.shape[0] < 2:
        return -1
    return 1 if np.nanmean(lat2d[-1, :] - lat2d[0, :]) > 0 else -1


def is_global_periodic(lon2d, lat2d=None, tol_frac: float = 0.5) -> bool:
    """True when the longitude axis wraps around the globe without a seam column.

    Works for any longitude convention: the columns are sorted on the circle
    and the axis is periodic when no angular gap (including the one across the
    wrap) is larger than the grid spacing.  A duplicated seam column (0 and 360
    both present) is not seamless.  Curvilinear grids are never periodic.
    """
    lon2d = np.asarray(lon2d, dtype=float)
    if lon2d.ndim != 2 or lon2d.shape[1] < 3:
        return False
    if lat2d is not None and not is_regular(lon2d, lat2d):
        return False
    lons = np.sort(to_360(lon2d[0, :]))
    gaps = np.diff(lons)
    wrap_gap = lons[0] + 360.0 - lons[-1]
    if np.any(gaps < 1e-6):
        return False  # duplicated column (e.g. 0 and 360)
    dx = float(np.median(gaps))
    return float(max(gaps.max(), wrap_gap)) < (1.0 + tol_frac) * dx


@dataclass(frozen=True)
class Grid:
    """2-D horizontal grid with its conventions and metric terms."""

    lon: np.ndarray
    lat: np.ndarray

    def __post_init__(self):
        lon = np.asarray(self.lon, dtype=float)
        lat = np.asarray(self.lat, dtype=float)
        if lon.ndim != 2 or lat.ndim != 2 or lon.shape != lat.shape:
            raise ValueError(
                f"lon/lat must be 2-D arrays of equal shape, got {lon.shape}/{lat.shape}"
            )
        object.__setattr__(self, "lon", lon)
        object.__setattr__(self, "lat", lat)

    @property
    def shape(self) -> tuple[int, int]:
        return self.lat.shape

    @property
    def ny(self) -> int:
        return self.lat.shape[0]

    @property
    def nx(self) -> int:
        return self.lat.shape[1]

    @cached_property
    def is_regular(self) -> bool:
        return is_regular(self.lon, self.lat)

    @cached_property
    def lat_direction(self) -> int:
        return lat_direction(self.lat)

    @property
    def lat_ascending(self) -> bool:
        return self.lat_direction > 0

    @cached_property
    def lon_convention(self) -> str:
        return lon_convention(self.lon)

    @cached_property
    def is_global_periodic(self) -> bool:
        return is_global_periodic(self.lon, self.lat)

    @cached_property
    def _metrics(self):
        dx, dy, area, spacing = calc_grid_distance_area(self.lat, self.lon)
        area = np.where(area < 0, 0.0, area)
        return dx, dy, area, float(spacing)

    @property
    def dx(self) -> np.ndarray:
        """Zonal grid distance [m], shape (ny, nx)."""
        return self._metrics[0]

    @property
    def dy(self) -> np.ndarray:
        """Meridional grid distance [m], shape (ny, nx)."""
        return self._metrics[1]

    @property
    def cell_area(self) -> np.ndarray:
        """Grid-cell area [m²], shape (ny, nx)."""
        return self._metrics[2]

    @property
    def spacing(self) -> float:
        """Domain-mean grid distance [m]."""
        return self._metrics[3]

    @property
    def spacing_km(self) -> float:
        return self.spacing / 1000.0

    @property
    def lat_1d(self) -> np.ndarray:
        """1-D latitude (regular grids only)."""
        return self.lat[:, 0]

    @property
    def lon_1d(self) -> np.ndarray:
        """1-D longitude (regular grids only)."""
        return self.lon[0, :]

    def describe(self) -> str:
        kind = "regular lat/lon" if self.is_regular else "curvilinear"
        order = "ascending" if self.lat_ascending else "descending"
        periodic = ", global periodic" if self.is_global_periodic else ""
        return (
            f"{kind} {self.ny}×{self.nx}, ~{self.spacing_km:.1f} km, "
            f"lat {self.lat.min():.2f}..{self.lat.max():.2f} ({order}), "
            f"lon {self.lon.min():.2f}..{self.lon.max():.2f} ({self.lon_convention}{periodic})"
        )
