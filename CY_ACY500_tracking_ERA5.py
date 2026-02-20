#!/usr/bin/env python
"""
CY_ACY500_tracking_ERA5.py — 500 hPa Cyclone / Anticyclone tracking from ERA5.

For each annual ERA5 500 hPa file in ``data_era5/``:
  1. Loads geopotential height at 500 hPa.
  2. Computes a smoothed anomaly and labels cyclonic / anticyclonic objects.
  3. Writes a per-year NetCDF to ``data_tracking/``.

All parameters (thresholds, paths) are read from ``config.toml``.
"""
from glob import glob
import os
import time
import logging
import numpy as np

import xarray as xr
import pandas as pd

from joblib import Parallel, delayed

import atmotrack_config as cfg
from tracking_functions import CY_ACY_z500_tracking
from utils import get_logger


###########################################################
def main():
    """Loop over available annual files and track upper-level CY/ACY in parallel."""
    os.makedirs(cfg.data_tracking, exist_ok=True)
    filesin = sorted(glob(f"{cfg.data_era5}/era5_daily_500hPa_????.nc"))
    Parallel(n_jobs=-1)(delayed(cy_z500_tracking)(fin_name) for fin_name in filesin)


###########################################################
def cy_z500_tracking(z500_finname):
    """Track 500 hPa cyclones/anticyclones for a single annual file."""
    logger = get_logger("atmotrack", log_file="out.log")
    logger.info(f"Analyzing {z500_finname}")
    start_time = time.time()

    z500 = xr.open_dataset(z500_finname).squeeze()
    z500_data = z500.z.values

    lat = z500.latitude.values
    lon = z500.longitude.values
    lon2d, lat2d = np.meshgrid(lon, lat)

    times = pd.date_range(
        z500.valid_time.isel(valid_time=0).values,
        end=z500.valid_time.isel(valid_time=-1).values,
        freq="6h",
    )

    logging.debug(f"Loading data: {time.time() - start_time:.2f} s")

    fileout = z500_finname.replace("500hPa", "cy_z500").replace(
        cfg.data_era5, cfg.data_tracking
    )

    _, _ = CY_ACY_z500_tracking(z500_data, times, lon2d, lat2d, nc_file=fileout)

    logger.info(f"DONE {z500_finname} in {time.time() - start_time:.2f} s")


###############################################################################
if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO,
    )
    main()
