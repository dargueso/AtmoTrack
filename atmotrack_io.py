"""
atmotrack_io.py — Source-agnostic NetCDF loader for AtmoTrack.

Provides a thin abstraction over xarray so that entry scripts work with any
CF-compliant NetCDF collection, not just ERA5 annual files.  All I/O
configuration is read from ``config.toml`` via ``atmotrack_config``.

Supported layouts
-----------------
* ERA5 style — 1-D ``latitude`` / ``longitude`` coordinates, ``valid_time``
  time dimension, one file per year.
* WRF style — 2-D ``XLAT`` / ``XLONG`` arrays (Lambert Conformal or any other
  projection), ``time`` dimension, arbitrary per-file time spans.
* Any other CF-compliant dataset: set ``lat_var``, ``lon_var``, ``lat_is_2d``,
  ``time_var``, and the glob ``pattern_*`` / ``var_*`` keys in ``config.toml``.
"""

import pathlib
from glob import glob

import numpy as np
import pandas as pd
import xarray as xr

import atmotrack_config as cfg


def open_pattern(pattern_key: str) -> xr.Dataset:
    """Open all files matching ``cfg.<pattern_key>`` as one merged dataset.

    Files are discovered with :func:`glob.glob` relative to ``cfg.data_input``,
    sorted by name, and opened with :func:`xarray.open_mfdataset` (or
    :func:`xarray.open_dataset` when only a single file is found).

    Parameters
    ----------
    pattern_key:
        Name of the ``atmotrack_config`` attribute that holds the glob pattern,
        e.g. ``"pattern_z500"`` → ``cfg.pattern_z500``.

    Raises
    ------
    FileNotFoundError
        If the glob returns no files.
    """
    pattern = getattr(cfg, pattern_key)
    data_dir = pathlib.Path(cfg.data_input)
    files = sorted(glob(str(data_dir / pattern)))
    if not files:
        raise FileNotFoundError(
            f"No files found matching pattern '{pattern}' in '{data_dir}'. "
            f"Check cfg.data_input and cfg.{pattern_key} in config.toml."
        )
    if len(files) == 1:
        return xr.open_dataset(files[0])
    return xr.open_mfdataset(files, combine="by_coords")


def available_years(ds: xr.Dataset) -> list:
    """Return a sorted list of calendar years present in ``ds``.

    Years are extracted from the CF-compliant time coordinate named
    ``cfg.time_var``.
    """
    return sorted({int(y) for y in ds[cfg.time_var].dt.year.values})


def slice_year(ds: xr.Dataset, year: int) -> xr.Dataset:
    """Extract one calendar year from *ds* and squeeze length-1 dimensions.

    Parameters
    ----------
    ds:
        Dataset returned by :func:`open_pattern`.
    year:
        Four-digit calendar year to extract.
    """
    return ds.sel({cfg.time_var: ds[cfg.time_var].dt.year == year}).squeeze()


def load_grid(ds: xr.Dataset):
    """Return ``(lon2d, lat2d)`` numpy arrays from *ds*.

    ERA5 style (``cfg.lat_is_2d = false``):
        Reads 1-D ``cfg.lat_var`` and ``cfg.lon_var`` arrays and expands them
        with :func:`numpy.meshgrid`, returning arrays of shape ``(ny, nx)``.

    WRF / projected style (``cfg.lat_is_2d = true``):
        Reads the 2-D ``cfg.lat_var`` and ``cfg.lon_var`` arrays directly.
        Both must already have shape ``(ny, nx)``.

    Returns
    -------
    lon2d, lat2d : ndarray
        Both have shape ``(ny, nx)``.
    """
    lat = ds[cfg.lat_var].values
    lon = ds[cfg.lon_var].values
    if getattr(cfg, "lat_is_2d", False):
        return lon, lat  # already (ny, nx)
    return np.meshgrid(lon, lat)  # → lon2d, lat2d each (ny, nx)


def load_times(ds: xr.Dataset) -> pd.DatetimeIndex:
    """Return a :class:`pandas.DatetimeIndex` from *ds*'s time coordinate.

    The coordinate named ``cfg.time_var`` must be CF-compliant; xarray decodes
    it automatically, so no manual unit conversion is needed.
    """
    return pd.DatetimeIndex(ds[cfg.time_var].values)
