#!/usr/bin/env python
"""
TC_tracking_ERA5.py — Tropical Cyclone tracking from ERA5 data.

For each annual SLP cyclone file in ``data_tracking/``:
  1. Loads pre-computed SLP cyclone objects (cy_slp_objects).
  2. Loads 850 hPa temperature and SLP from ERA5 input files.
  3. Runs TC_tracking to filter confirmed tropical cyclones.
  4. Writes a per-year NetCDF to ``data_tracking/``.

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
from tracking_functions import TC_tracking
from utils import get_logger


###########################################################
def main():
    """Loop over available annual CY-SLP files and track TCs in parallel."""
    parser = argparse.ArgumentParser(description="Tropical Cyclone tracking from ERA5 data.")
    parser.add_argument(
        "--year-start",
        type=int,
        default=1940,
        metavar="YEAR",
        help="First year to process (default: 1940)",
    )
    parser.add_argument(
        "--year-end",
        type=int,
        default=2024,
        metavar="YEAR",
        help="Last year to process (default: 2024)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG-level logging")
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=8,
        metavar="N",
        help="Number of parallel workers (default: 8)",
    )
    args = parser.parse_args()

    get_logger(
        "atmotrack", log_file="out.log", level=logging.DEBUG if args.verbose else logging.INFO
    )
    os.makedirs(cfg.data_tracking, exist_ok=True)
    filesin = sorted(
        f
        for f in glob(f"{cfg.data_tracking}/era5_daily_cy_slp_????.nc")
        if args.year_start <= int(f[-7:-3]) <= args.year_end
    )
    n_jobs = min(len(filesin), args.jobs)
    # Limit BLAS threads to 1 per worker to avoid thread contention on
    # many-core machines where numpy/scipy would otherwise claim all CPUs.
    for _var in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[_var] = "1"
    Parallel(n_jobs=n_jobs)(delayed(tc_tracking_worker)(fin, args.verbose) for fin in filesin)


###########################################################
def tc_tracking_worker(cy_slp_finname, verbose=False):
    """Track tropical cyclones for a single annual file."""
    logger = get_logger("atmotrack", level=logging.DEBUG if verbose else logging.INFO)
    logger.info(f"Analyzing {cy_slp_finname}")
    start_time = time.time()

    # Derive year from filename (last 4 chars before .nc)
    year = cy_slp_finname[-7:-3]

    # Load pre-computed SLP cyclone objects
    ds_cy = xr.open_dataset(cy_slp_finname).squeeze()
    cy_slp_objects = ds_cy.cy_slp_objects.values.astype(int)

    # Load 850 hPa temperature
    fin_850 = f"{cfg.data_era5}/era5_daily_850hPa_{year}.nc"
    ds850 = xr.open_dataset(fin_850).squeeze()
    t850_data = ds850.t.values

    # Load SLP (Pa)
    fin_slp = f"{cfg.data_era5}/era5_daily_SLP_{year}.nc"
    ds_slp = xr.open_dataset(fin_slp).squeeze()
    slp_data = ds_slp.msl.values

    # Build coordinate grids and time axis
    lat = ds_slp.latitude.values
    lon = ds_slp.longitude.values
    lon2d, lat2d = np.meshgrid(lon, lat)

    times = pd.date_range(
        ds_slp.valid_time.isel(valid_time=0).values,
        end=ds_slp.valid_time.isel(valid_time=-1).values,
        freq="6h",
    )

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout_tc = f"{cfg.data_tracking}/era5_daily_tc_{year}.nc"

    TC_tracking(cy_slp_objects, t850_data, slp_data, lon2d, lat2d, times=times, nc_file=fileout_tc)

    logger.info(f"DONE {cy_slp_finname} in {time.time() - start_time:.2f} s")


###############################################################################
if __name__ == "__main__":
    main()
