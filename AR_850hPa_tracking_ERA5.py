#!/usr/bin/env python
"""
AR_850hPa_tracking_ERA5.py — Atmospheric River tracking from ERA5 850 hPa moisture flux.

For each annual ERA5 850 hPa file in ``data_era5/``:
  1. Loads u, v wind and q specific humidity at 850 hPa.
  2. Computes moisture flux magnitude: sqrt((u·q)² + (v·q)²).
  3. Runs AR_850hPa_tracking to label AR objects.
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
from tracking_functions import AR_850hPa_tracking
from utils import get_logger


###########################################################
def main():
    """Loop over available annual 850 hPa files and track ARs in parallel."""
    parser = argparse.ArgumentParser(description="AR tracking from ERA5 850 hPa moisture flux.")
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
        for f in glob(f"{cfg.data_era5}/era5_daily_850hPa_????.nc")
        if args.year_start <= int(f[-7:-3]) <= args.year_end
    )
    n_jobs = min(len(filesin), args.jobs)
    for _var in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[_var] = "1"
    Parallel(n_jobs=n_jobs)(delayed(ar850_tracking_worker)(fin, args.verbose) for fin in filesin)


###########################################################
def ar850_tracking_worker(fin_850hpa, verbose=False):
    """Track 850 hPa ARs for a single annual file."""
    logger = get_logger("atmotrack", level=logging.DEBUG if verbose else logging.INFO)
    logger.info(f"Analyzing {fin_850hpa}")
    start_time = time.time()

    ds = xr.open_dataset(fin_850hpa).squeeze()

    u850 = ds.u.values
    v850 = ds.v.values
    q850 = ds.q.values  # specific humidity [kg/kg]; treated as g/g for magnitude

    # 850 hPa moisture flux magnitude
    VapTrans = np.sqrt((u850 * q850) ** 2 + (v850 * q850) ** 2)

    lat = ds.latitude.values
    lon = ds.longitude.values
    lon2d, lat2d = np.meshgrid(lon, lat)

    times = pd.date_range(
        ds.valid_time.isel(valid_time=0).values,
        end=ds.valid_time.isel(valid_time=-1).values,
        freq="6h",
    )

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout = fin_850hpa.replace("850hPa", "ar850").replace(cfg.data_era5, cfg.data_tracking)

    AR_850hPa_tracking(VapTrans, times, lon2d, lat2d, nc_file=fileout)

    logger.info(f"DONE {fin_850hpa} in {time.time() - start_time:.2f} s")


###############################################################################
if __name__ == "__main__":
    main()
