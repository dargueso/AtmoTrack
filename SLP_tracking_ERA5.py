#!/usr/bin/env python
"""
SLP_tracking_ERA5.py — Surface Cyclone / Anticyclone tracking from ERA5 SLP.

For each annual ERA5 SLP file in ``data_era5/``:
  1. Loads mean sea-level pressure (msl, Pa).
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
from tracking_functions import CY_ACY_slp_tracking
from utils import get_logger


###########################################################
def main():
    """Loop over available annual SLP files and track surface CY/ACY in parallel."""
    parser = argparse.ArgumentParser(description="Surface CY/ACY tracking from ERA5 SLP.")
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
        f for f in glob(f"{cfg.data_era5}/era5_daily_SLP_????.nc")
        if args.year_start <= int(f[-7:-3]) <= args.year_end
    )
    n_jobs = min(len(filesin), os.cpu_count() or 1)
    Parallel(n_jobs=n_jobs)(delayed(slp_tracking)(fin_name, args.verbose) for fin_name in filesin)


###########################################################
def slp_tracking(slp_finname, verbose=False):
    """Track surface cyclones/anticyclones for a single annual file."""
    logger = get_logger("atmotrack", level=logging.DEBUG if verbose else logging.INFO)
    logger.info(f"Analyzing {slp_finname}")
    start_time = time.time()

    ds = xr.open_dataset(slp_finname).squeeze()

    # msl is in Pa; conversion to hPa happens inside CY_ACY_slp_tracking
    slp_data = ds.msl.values

    lat = ds.latitude.values
    lon = ds.longitude.values
    lon2d, lat2d = np.meshgrid(lon, lat)

    times = pd.date_range(
        ds.valid_time.isel(valid_time=0).values,
        end=ds.valid_time.isel(valid_time=-1).values,
        freq="6h",
    )

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")

    fileout = slp_finname.replace("SLP", "cy_slp").replace(cfg.data_era5, cfg.data_tracking)

    _, _ = CY_ACY_slp_tracking(slp_data, times, lon2d, lat2d, nc_file=fileout)

    logger.info(f"DONE {slp_finname} in {time.time() - start_time:.2f} s")


###############################################################################
if __name__ == "__main__":
    main()
