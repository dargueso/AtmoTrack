"""File index: per-year file selection, cache invalidation, stream alignment."""

import json
import os

import numpy as np
import pandas as pd
import xarray as xr

from atmotrack import config as cfg
from atmotrack import io as aio
from tests.conftest import write_era5_like


def _collection(tmp_path):
    """Three yearly files plus one file spanning two years, all 6-hourly."""
    lat = np.array([50.0, 49.5, 49.0])
    lon = np.array([0.0, 0.5, 1.0, 1.5])
    files = []
    for year in (2000, 2001, 2002):
        t = pd.date_range(f"{year}-01-01", f"{year}-12-31 18:00", freq="6h")
        files.append(
            write_era5_like(
                tmp_path / f"era5_daily_500hPa_{year}.nc",
                t,
                lon,
                lat,
                {"z": np.full((len(t), 3, 4), 50_000.0 + year)},
            )
        )
    t = pd.date_range("2003-01-01", "2004-12-31 18:00", freq="6h")
    files.append(
        write_era5_like(
            tmp_path / "era5_daily_500hPa_2003-2004.nc",
            t,
            lon,
            lat,
            {"z": np.full((len(t), 3, 4), 52_003.0)},
        )
    )
    return files


def test_index_and_open_years(tmp_path, use_config):
    _collection(tmp_path)
    use_config(data_input=str(tmp_path), DT=6)

    years = aio.available_years("pattern_z500")
    assert years == [2000, 2001, 2002, 2003, 2004]

    assert [os.path.basename(f) for f in aio.files_for_year("pattern_z500", 2001)] == [
        "era5_daily_500hPa_2001.nc"
    ]
    assert [os.path.basename(f) for f in aio.files_for_year("pattern_z500", 2004)] == [
        "era5_daily_500hPa_2003-2004.nc"
    ]
    # a 48 h halo around 2001 pulls in the neighbouring yearly files
    assert len(aio.files_for_year("pattern_z500", 2001, halo_h=48)) == 3

    ds = aio.open_years("pattern_z500", 2004)
    assert int(ds[cfg.time_var].dt.year.min()) == 2004 == int(ds[cfg.time_var].dt.year.max())
    assert ds.sizes[cfg.time_var] == 366 * 4
    assert float(ds["z"].isel({cfg.time_var: 0, "latitude": 0, "longitude": 0})) == 52_003.0

    # cache exists next to the data and is reused; touching a file invalidates its entry
    cache = tmp_path / aio.INDEX_NAME
    assert cache.is_file()
    before = json.loads(cache.read_text())["files"]
    target = str(tmp_path / "era5_daily_500hPa_2000.nc")
    os.utime(target, None)
    aio.index_pattern("pattern_z500")
    after = json.loads(cache.read_text())["files"]
    assert after[target]["mtime"] != before[target]["mtime"]
    assert after[target]["t0"] == before[target]["t0"]


def test_subsample_and_accumulate(tmp_path, use_config):
    use_config(data_input=str(tmp_path), DT=6)
    lat = np.array([50.0, 49.5])
    lon = np.array([0.0, 0.5])
    t = pd.date_range("2000-01-01", periods=48, freq="1h")
    hourly = np.arange(48, dtype=float)[:, None, None] * np.ones((1, 2, 2))
    da = xr.DataArray(
        hourly,
        dims=(cfg.time_var, "latitude", "longitude"),
        coords={cfg.time_var: t, "latitude": lat, "longitude": lon},
        name="x",
    )

    sub = aio.subsample_to_dt(da, 6)
    assert list(sub[cfg.time_var].dt.hour.values[:4]) == [0, 6, 12, 18]
    assert sub.sizes[cfg.time_var] == 8

    total, peak = aio.accumulate_to_dt(da, 6)
    assert total.sizes[cfg.time_var] == 8
    # window [00, 06) sums hours 0..5
    assert float(total.isel({cfg.time_var: 0})[0, 0]) == sum(range(6))
    assert float(peak.isel({cfg.time_var: 0})[0, 0]) == 5
    assert str(total[cfg.time_var].values[1])[:13] == "2000-01-01T06"

    # a rate in mm/s over 1 h steps -> amounts per step, then summed
    total_rate, _ = aio.accumulate_to_dt(da, 6, kind="rate")
    assert float(total_rate.isel({cfg.time_var: 0})[0, 0]) == sum(range(6)) * 3600

    # a step that does not divide DT is refused
    t4 = pd.date_range("2000-01-01", periods=12, freq="4h")
    da4 = xr.DataArray(
        np.ones((12, 2, 2)),
        dims=(cfg.time_var, "latitude", "longitude"),
        coords={cfg.time_var: t4, "latitude": lat, "longitude": lon},
        name="x",
    )
    try:
        aio.subsample_to_dt(da4, 6)
    except ValueError as exc:
        assert "divisor" in str(exc)
    else:
        raise AssertionError("4 h -> 6 h must be refused")
