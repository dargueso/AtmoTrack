#!/usr/bin/env python
"""
CY_ACY500_tracking_ERA5.py — 500 hPa Cyclone / Anticyclone tracking from ERA5.

For each annual ERA5 500 hPa file in ``data_era5/``:
  1. Loads geopotential height at 500 hPa.
  2. Computes a smoothed anomaly and labels cyclonic / anticyclonic objects.
  3. Writes a per-year NetCDF to ``data_tracking/``.

All parameters (thresholds, paths) are read from ``config.toml``.
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
from tracking_functions import CY_ACY_z500_tracking
from utils import get_logger


###########################################################
def main():
    """Loop over available annual files and track upper-level CY/ACY in parallel."""
    parser = argparse.ArgumentParser(description="500 hPa CY/ACY tracking from ERA5 data.")
    parser.add_argument("--year-start", type=int, default=1940, metavar="YEAR",
                        help="First year to process (default: 1940)")
    parser.add_argument("--year-end", type=int, default=2024, metavar="YEAR",
                        help="Last year to process (default: 2024)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable DEBUG-level logging")
    parser.add_argument("--jobs", "-j", type=int, default=8, metavar="N",
                        help="Number of parallel workers (default: 8)")
    args = parser.parse_args()

    get_logger("atmotrack", log_file="out.log",
               level=logging.DEBUG if args.verbose else logging.INFO)
    os.makedirs(cfg.data_tracking, exist_ok=True)
    filesin = sorted(
        f for f in glob(f"{cfg.data_era5}/era5_daily_500hPa_????.nc")
        if args.year_start <= int(f[-7:-3]) <= args.year_end
    )
    n_jobs = min(len(filesin), args.jobs)
    for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[_var] = "1"
    Parallel(n_jobs=n_jobs)(delayed(cy_z500_tracking)(fin_name, args.verbose) for fin_name in filesin)


###########################################################
def cy_z500_tracking(z500_finname, verbose=False):
    """Track 500 hPa cyclones/anticyclones for a single annual file."""
    logger = get_logger("atmotrack", level=logging.DEBUG if verbose else logging.INFO)
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

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")

    fileout = z500_finname.replace("500hPa", "cy_z500").replace(cfg.data_era5, cfg.data_tracking)

    _, _ = CY_ACY_z500_tracking(z500_data, times, lon2d, lat2d, nc_file=fileout)

    logger.info(f"DONE {z500_finname} in {time.time() - start_time:.2f} s")


###############################################################################
if __name__ == "__main__":
    main()
