"""pytest configuration and shared fixtures for AtmoTrack.

The package is installed (``pip install -e .``), so no path tweaks are needed.
Tests run against the packaged default ``config.toml`` (DT = 6 h) unless the
working directory holds one or ``ATMOTRACK_CONFIG`` is set.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

# Synthetic ERA5-like grid: 0.5°, 75→10°N (descending), −30→20°E
LAT = np.arange(75.0, 9.75, -0.5)
LON = np.arange(-30.0, 20.25, 0.5)
LON2D, LAT2D = np.meshgrid(LON, LAT)
TIMES = pd.date_range("2000-01-01 00:00", periods=28, freq="6h")
NT, NLAT, NLON = len(TIMES), len(LAT), len(LON)


def z500_field(lon2d=LON2D, lat2d=LAT2D, nt=NT, lat_c=40.0):
    """Geopotential [m²/s²] with a deep trough drifting east across the COL region."""
    z = np.empty((nt,) + lon2d.shape)
    for t in range(nt):
        lon_c = -12.0 + t * 0.4
        dist2 = (lon2d - lon_c) ** 2 + (lat2d - lat_c) ** 2
        z[t] = 53_000.0 - 2_500.0 * np.exp(-dist2 / 20.0) + 500.0 * np.sin(2 * np.pi * lon2d / 8.0)
    return z


def slp_field(lon2d=LON2D, lat2d=LAT2D, nt=NT):
    """Mean sea-level pressure [Pa] with a ~20 hPa low drifting east."""
    slp = np.empty((nt,) + lon2d.shape)
    for t in range(nt):
        lon_c = -8.0 + t * 0.3
        dist2 = (lon2d - lon_c) ** 2 + (lat2d - 42.0) ** 2
        slp[t] = 101_325.0 - 2_000.0 * np.exp(-dist2 / 15.0)
    return slp


@pytest.fixture
def rng():
    return np.random.default_rng(42)


@pytest.fixture
def use_config(tmp_path, monkeypatch):
    """Factory: write a config with the given key overrides and load it.

    The packaged default is the base.  Returns the config path.  The default
    config is restored afterwards.
    """
    from atmotrack import config as cfg

    def _make(**overrides):
        import re

        text = cfg.default_path().read_text()
        for key, value in overrides.items():
            if isinstance(value, str):
                rep = f'{key} = "{value}"'
            elif isinstance(value, bool):
                rep = f"{key} = {str(value).lower()}"
            else:
                rep = f"{key} = {value}"
            text, n = re.subn(rf"(?m)^{re.escape(key)}\s*=.*$", rep, text)
            if n == 0:
                text += f"\n{rep}\n"
        path = tmp_path / "config.toml"
        path.write_text(text)
        cfg.load(path)
        return path

    yield _make
    cfg.load(cfg.default_path())


def write_era5_like(
    path,
    times,
    lon,
    lat,
    variables: dict,
    time_var="valid_time",
    lon_name="longitude",
    lat_name="latitude",
    attrs=None,
):
    """Write a 1-D-coordinate NetCDF file like ERA5 (dims time, lat, lon)."""
    data_vars = {
        name: (
            (time_var, lat_name, lon_name),
            np.asarray(values, dtype=np.float32),
            (attrs or {}).get(name, {}),
        )
        for name, values in variables.items()
    }
    ds = xr.Dataset(data_vars, coords={time_var: times, lat_name: lat, lon_name: lon})
    ds.to_netcdf(path)
    return path
