#!/usr/bin/env python
"""
COL_tracking_ERA5.py — Cut-Off Low tracking from any CF-compliant dataset.

For each year in the configured input dataset:
  1. Loads 500, 200, 850 hPa geopotential/wind/temperature and precipitation.
  2. Runs CY_ACY_z500_tracking to identify upper-level cyclones.
  3. Runs COL_tracking to classify cut-off lows within a configured region.
  4. Writes a per-year NetCDF to ``data_tracking/``.

All parameters (thresholds, domain, paths, variable names) are read from
``config.toml``.  Set ``[data_source]`` keys to switch between ERA5, WRF, etc.
"""

import argparse
import logging
import os
import pathlib
import time

import numpy as np
from joblib import Parallel, delayed

import atmotrack_config as cfg
from atmotrack_io import available_years, load_grid, load_times, open_pattern, slice_year
from tracking_functions import COL_tracking, CY_ACY_z500_tracking
from utils import get_logger


###########################################################
def main():
    """Loop over available years and track COLs in parallel."""
    parser = argparse.ArgumentParser(description="COL tracking from input data.")
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

    ds_z500 = open_pattern("pattern_z500")
    years = [y for y in available_years(ds_z500) if args.year_start <= y <= args.year_end]
    ds_z500.close()

    n_jobs = min(len(years), args.jobs)
    # Limit BLAS threads to 1 per worker to avoid thread contention on
    # many-core machines where numpy/scipy would otherwise claim all CPUs.
    for _var in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[_var] = "1"
    Parallel(n_jobs=n_jobs)(delayed(cutofflow_tracking)(year, args.verbose) for year in years)


###########################################################
def cutofflow_tracking(year: int, verbose: bool = False) -> None:
    """Track COLs for a single year."""
    logger = get_logger("atmotrack", level=logging.DEBUG if verbose else logging.INFO)
    logger.info(f"Analyzing year {year}")
    start_time = time.time()

    z500 = slice_year(open_pattern("pattern_z500"), year)
    z200 = slice_year(open_pattern("pattern_z200"), year)
    z850 = slice_year(open_pattern("pattern_z850"), year)
    pr = slice_year(open_pattern("pattern_pr"), year)

    z500_data = z500[cfg.var_z500].values
    u200_data = z200[cfg.var_u200].values
    t850_data = z850[cfg.var_t850].values
    u850_data = z850[cfg.var_u850].values
    v850_data = z850[cfg.var_v850].values
    pr_data = pr[cfg.var_pr].resample({cfg.time_var: "6h"}).sum().values * 1000.0
    pr_data_max = pr[cfg.var_pr].resample({cfg.time_var: "6h"}).max().values * 1000.0

    if z500_data.shape != pr_data.shape:
        logger.debug(f"WARNING: Data shapes do not match: {z500_data.shape} vs {pr_data.shape}")
        diff_times = z500_data.shape[0] - pr_data.shape[0]
        pr_data = np.pad(pr_data, ((diff_times, 0), (0, 0), (0, 0)), constant_values=0)
        pr_data_max = np.pad(pr_data_max, ((diff_times, 0), (0, 0), (0, 0)), constant_values=0)

    lon2d, lat2d = load_grid(z500)
    times = load_times(z500)

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout_col = pathlib.Path(cfg.data_tracking) / f"col_z500_{year:04d}.nc"

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
        nc_file=str(fileout_col),
    )

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


###############################################################################
if __name__ == "__main__":
    main()
