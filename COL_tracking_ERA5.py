#!/usr/bin/env python
"""
COL_tracking_ERA5.py — Cut-Off Low tracking from ERA5 data.

For each annual ERA5 file in ``data_era5/``:
  1. Loads 500, 200, 850 hPa geopotential/wind/temperature and precipitation.
  2. Runs CY_ACY_z500_tracking to identify upper-level cyclones.
  3. Runs COL_tracking to classify cut-off lows within a configured region.
  4. Writes a per-year NetCDF to ``data_tracking/``.

All parameters (thresholds, domain, paths) are read from ``config.toml``.
"""

import argparse
import logging
import os
import time
from glob import glob

import numpy as np
import pandas as pd
import xarray as xr
from joblib import Parallel, delayed

import atmotrack_config as cfg
from tracking_functions import COL_tracking, CY_ACY_z500_tracking
from utils import get_logger


###########################################################
def main():
    """Loop over available annual files and track COLs in parallel."""
    parser = argparse.ArgumentParser(description="COL tracking from ERA5 data.")
    parser.add_argument("--year-start", type=int, default=1940, metavar="YEAR",
                        help="First year to process (default: 1940)")
    parser.add_argument("--year-end", type=int, default=2024, metavar="YEAR",
                        help="Last year to process (default: 2024)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable DEBUG-level logging")
    args = parser.parse_args()

    get_logger("atmotrack", log_file="out.log",
               level=logging.DEBUG if args.verbose else logging.INFO)
    os.makedirs(cfg.data_tracking, exist_ok=True)
    filesin = sorted(
        f for f in glob(f"{cfg.data_era5}/era5_daily_500hPa_????.nc")
        if args.year_start <= int(f[-7:-3]) <= args.year_end
    )
    Parallel(n_jobs=-1)(delayed(cutofflow_tracking)(fin_name, args.verbose) for fin_name in filesin)


###########################################################
def cutofflow_tracking(z500_finname, verbose=False):
    """Track COLs for a single annual file."""
    logger = get_logger("atmotrack", log_file="out.log",
                        level=logging.DEBUG if verbose else logging.INFO)
    logger.info(f"Analyzing {z500_finname}")
    start_time = time.time()

    z500 = xr.open_dataset(z500_finname).squeeze()
    z200 = xr.open_dataset(z500_finname.replace("500hPa", "200hPa")).squeeze()
    z850 = xr.open_dataset(z500_finname.replace("500hPa", "850hPa")).squeeze()
    pr = xr.open_dataset(z500_finname.replace("500hPa", "PR")).squeeze()

    z500_data = z500.z.values
    u200_data = z200.u.values
    t850_data = z850.t.values
    u850_data = z850.u.values
    v850_data = z850.v.values
    pr_data = pr.tp.resample(valid_time="6h").sum().values * 1000.0
    pr_data_max = pr.tp.resample(valid_time="6h").max().values * 1000.0

    if z500_data.shape != pr_data.shape:
        logger.debug(f"WARNING: Data shapes do not match: {z500_data.shape} vs {pr_data.shape}")
        diff_times = z500_data.shape[0] - pr_data.shape[0]
        pr_data = np.pad(pr_data, ((diff_times, 0), (0, 0), (0, 0)), constant_values=0)
        pr_data_max = np.pad(pr_data_max, ((diff_times, 0), (0, 0), (0, 0)), constant_values=0)

    lat = z500.latitude.values
    lon = z500.longitude.values
    lon2d, lat2d = np.meshgrid(lon, lat)
    times = pd.date_range(
        z500.valid_time.isel(valid_time=0).values,
        end=z500.valid_time.isel(valid_time=-1).values,
        freq="6h",
    )

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout_col = z500_finname.replace("500hPa", "col_z500").replace(
        cfg.data_era5, cfg.data_tracking
    )

    cy_z500_objects, _ = CY_ACY_z500_tracking(z500_data, times, lon2d, lat2d, nc_file=None)

    _ = COL_tracking(
        cy_z500_objects,
        z500_data,
        u200_data,
        u850_data,
        v850_data,
        t850_data,
        pr_data,
        pr_data_max,
        times=times,
        Lon=lon2d,
        Lat=lat2d,
        nc_file=fileout_col,
    )

    logger.info(f"DONE {z500_finname} in {time.time() - start_time:.2f} s")


###############################################################################
if __name__ == "__main__":
    main()
