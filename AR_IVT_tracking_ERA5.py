#!/usr/bin/env python
"""
AR_IVT_tracking_ERA5.py — Atmospheric River tracking from Integrated Vapour Transport.

For each year in the configured input dataset:
  1. Loads eastward (ivte) and northward (ivtn) IVT components [kg m⁻¹ s⁻¹].
  2. Computes IVT magnitude: sqrt(ivte² + ivtn²).
  3. Runs AR_IVT_tracking to label AR objects.
  4. Writes a per-year NetCDF to ``data_tracking/``.

All parameters (thresholds, paths, variable names) are read from ``config.toml``.
Set ``[data_source]`` keys to switch between ERA5, WRF, etc.
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
from tracking_functions import AR_IVT_tracking
from utils import get_logger


###########################################################
def main():
    """Loop over available years and track ARs in parallel."""
    parser = argparse.ArgumentParser(description="AR tracking from IVT.")
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

    ds_ivt = open_pattern("pattern_ivt")
    all_years = available_years(ds_ivt)
    years = [y for y in all_years if args.year_start <= y <= args.year_end]
    ds_ivt.close()

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
    Parallel(n_jobs=n_jobs)(delayed(ar_ivt_tracking_worker)(year, args.verbose) for year in years)


###########################################################
def ar_ivt_tracking_worker(year: int, verbose: bool = False) -> None:
    """Track IVT ARs for a single year."""
    logger = get_logger("atmotrack", level=logging.DEBUG if verbose else logging.INFO)
    logger.info(f"Analyzing year {year}")
    start_time = time.time()

    ds = slice_year(open_pattern("pattern_ivt"), year)

    ivte = ds[cfg.var_ivte].values  # eastward IVT [kg m⁻¹ s⁻¹]
    ivtn = ds[cfg.var_ivtn].values  # northward IVT [kg m⁻¹ s⁻¹]
    IVT = np.sqrt(ivte**2 + ivtn**2)

    lon2d, lat2d = load_grid(ds)
    times = load_times(ds)

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout = pathlib.Path(cfg.data_tracking) / f"ar_ivt_{year:04d}.nc"

    AR_IVT_tracking(IVT, times, lon2d, lat2d, nc_file=str(fileout))

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


###############################################################################
if __name__ == "__main__":
    main()
