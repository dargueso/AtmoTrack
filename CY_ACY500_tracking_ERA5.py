#!/usr/bin/env python
"""
CY_ACY500_tracking_ERA5.py — 500 hPa Cyclone / Anticyclone tracking.

For each year in the configured input dataset:
  1. Loads geopotential at 500 hPa.
  2. Computes a smoothed anomaly and labels cyclonic / anticyclonic objects.
  3. Writes a per-year NetCDF to ``data_tracking/``.

All parameters (thresholds, paths, variable names) are read from ``config.toml``.
Set ``[data_source]`` keys to switch between ERA5, WRF, etc.
"""

import argparse
import logging
import os
import pathlib
import time

from joblib import Parallel, delayed

import atmotrack_config as cfg
from atmotrack_io import available_years, infer_dt, load_grid, load_times, open_pattern, slice_year
from tracking_functions import CY_ACY_z500_tracking
from utils import get_logger


###########################################################
def main():
    """Loop over available years and track upper-level CY/ACY in parallel."""
    parser = argparse.ArgumentParser(description="500 hPa CY/ACY tracking.")
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
    all_years = available_years(ds_z500)
    years = [y for y in all_years if args.year_start <= y <= args.year_end]
    ds_z500.close()

    if not years:
        get_logger("atmotrack").warning(
            f"No data found for {args.year_start}–{args.year_end}. "
            f"Available years in input files: {all_years}"
        )
        return

    n_jobs = min(len(years), args.jobs)
    for _var in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[_var] = "1"
    Parallel(n_jobs=n_jobs)(delayed(cy_z500_tracking)(year, args.verbose) for year in years)


###########################################################
def cy_z500_tracking(year: int, verbose: bool = False) -> None:
    """Track 500 hPa cyclones/anticyclones for a single year."""
    logger = get_logger("atmotrack", level=logging.DEBUG if verbose else logging.INFO)
    logger.info(f"Analyzing year {year}")
    start_time = time.time()

    ds = slice_year(open_pattern("pattern_z500"), year)
    z500_data = ds[cfg.var_z500].values

    lon2d, lat2d = load_grid(ds)
    times = load_times(ds)
    cfg.DT = infer_dt(times)

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")

    fileout = pathlib.Path(cfg.data_tracking) / f"cy_z500_{year:04d}.nc"

    _, _ = CY_ACY_z500_tracking(z500_data, times, lon2d, lat2d, nc_file=str(fileout))

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


###############################################################################
if __name__ == "__main__":
    main()
