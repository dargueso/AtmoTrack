"""Writer for the per-year tracking NetCDF files.

All trackers write through :func:`write_tracking_file` so that outputs share
one layout:

* regular lat/lon grids: dims ``(time, latitude, longitude)`` with 1-D
  ``latitude``/``longitude`` coordinates **and** 2-D ``lat``/``lon``;
* curvilinear grids: dims ``(time, y, x)`` with 2-D ``lat``/``lon`` only;
* ``time`` encoded with the calendar of the input (standard or cftime).
"""

from __future__ import annotations

import logging
import time as _time

import numpy as np
import xarray as xr

from atmotrack.grid import Grid
from atmotrack.timeaxis import encode_times

logger = logging.getLogger("atmotrack")

LABEL_DTYPE = np.int32
FIELD_DTYPE = np.float32


def _as_grid(grid_or_lon, lat=None) -> Grid:
    if isinstance(grid_or_lon, Grid):
        return grid_or_lon
    return Grid(grid_or_lon, lat)


def write_tracking_file(
    path,
    times,
    grid,
    variables: dict,
    lat=None,
    global_attrs: dict | None = None,
    complevel: int = 4,
) -> None:
    """Write *variables* (``name -> array`` or ``name -> (array, attrs)``) to *path*.

    Arrays have shape ``(time, ny, nx)``.  Integer arrays are stored as int32
    (object labels), floats as float32.  *grid* is a :class:`Grid` or the 2-D
    longitude array (then *lat* must be given).
    """
    start = _time.time()
    g = _as_grid(grid, lat)

    if g.is_regular:
        dims = ("time", "latitude", "longitude")
        coords = {
            "time": ("time", np.asarray(times.values if hasattr(times, "values") else times)),
            "latitude": (
                "latitude",
                g.lat_1d,
                {"units": "degrees_north", "standard_name": "latitude"},
            ),
            "longitude": (
                "longitude",
                g.lon_1d,
                {"units": "degrees_east", "standard_name": "longitude"},
            ),
            "lat": (("latitude", "longitude"), g.lat, {"units": "degrees_north"}),
            "lon": (("latitude", "longitude"), g.lon, {"units": "degrees_east"}),
        }
    else:
        dims = ("time", "y", "x")
        coords = {
            "time": ("time", np.asarray(times.values if hasattr(times, "values") else times)),
            "lat": (("y", "x"), g.lat, {"units": "degrees_north", "standard_name": "latitude"}),
            "lon": (("y", "x"), g.lon, {"units": "degrees_east", "standard_name": "longitude"}),
        }

    data_vars = {}
    encoding = {"time": encode_times(times)}
    for name, value in variables.items():
        if isinstance(value, tuple):
            array, attrs = value
        else:
            array, attrs = value, {}
        array = np.asarray(array)
        if array.shape != (len(times), g.ny, g.nx):
            raise ValueError(
                f"{name}: shape {array.shape} does not match (time, ny, nx) = "
                f"({len(times)}, {g.ny}, {g.nx})"
            )
        if np.issubdtype(array.dtype, np.integer) or array.dtype == bool:
            array = array.astype(LABEL_DTYPE)
        else:
            array = array.astype(FIELD_DTYPE)
        data_vars[name] = (dims, array, dict(attrs))
        encoding[name] = {"zlib": True, "complevel": complevel}

    attrs = {"Conventions": "CF-1.8", "source": "AtmoTrack"}
    attrs.update(global_attrs or {})
    try:
        from atmotrack import __version__

        attrs.setdefault("atmotrack_version", __version__)
    except Exception:  # pragma: no cover
        pass

    ds = xr.Dataset(data_vars, coords=coords, attrs=attrs)
    ds.to_netcdf(path, mode="w", format="NETCDF4", encoding=encoding)
    logger.debug(f"======> Writing {path}: {_time.time() - start:.2f} s")
