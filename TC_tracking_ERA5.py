#!/usr/bin/env python
"""
TC_tracking_ERA5.py — Tropical Cyclone tracking.

Reads pre-computed SLP cyclone objects produced by SLP_tracking_ERA5.py, then
loads 850 hPa temperature and SLP from the configured input dataset to apply
tropical cyclone criteria.

For each year with an existing ``cy_slp_{year}.nc`` file:
  1. Loads pre-computed SLP cyclone objects.
  2. Loads 850 hPa temperature and SLP from the configured input source.
  3. Runs TC_tracking to filter confirmed tropical cyclones.
  4. Writes a per-year NetCDF to ``data_tracking/``.

All parameters (thresholds, paths, variable names) are read from ``config.toml``.
Set ``[data_source]`` keys to switch between ERA5, WRF, etc.

Note: run SLP_tracking_ERA5.py first to generate the required cy_slp files.
"""

import argparse
import logging
import os
import pathlib
import time

import xarray as xr
from joblib import Parallel, delayed

import atmotrack_config as cfg
from atmotrack_io import load_grid, load_times, open_pattern, slice_year
from tracking_functions import TC_tracking
from utils import get_logger


###########################################################
def main():
    """Loop over available CY-SLP files and track TCs in parallel."""
    parser = argparse.ArgumentParser(description="Tropical Cyclone tracking.")
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

    # Discover years from existing cy_slp output files (produced by SLP_tracking_ERA5.py)
    cy_slp_dir = pathlib.Path(cfg.data_tracking)
    cy_slp_files = sorted(cy_slp_dir.glob("cy_slp_????.nc"))
    years = [
        int(f.stem[-4:])
        for f in cy_slp_files
        if args.year_start <= int(f.stem[-4:]) <= args.year_end
    ]

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
    Parallel(n_jobs=n_jobs)(delayed(tc_tracking_worker)(year, args.verbose) for year in years)


###########################################################
def tc_tracking_worker(year: int, verbose: bool = False) -> None:
    """Track tropical cyclones for a single year."""
    logger = get_logger("atmotrack", level=logging.DEBUG if verbose else logging.INFO)
    logger.info(f"Analyzing year {year}")
    start_time = time.time()

    # Load pre-computed SLP cyclone objects (internal tracking output)
    cy_slp_path = pathlib.Path(cfg.data_tracking) / f"cy_slp_{year:04d}.nc"
    ds_cy = xr.open_dataset(cy_slp_path).squeeze()
    cy_slp_objects = ds_cy.cy_slp_objects.values.astype(int)

    # Load 850 hPa temperature and SLP from configured input source
    ds_850 = slice_year(open_pattern("pattern_z850"), year)
    ds_slp = slice_year(open_pattern("pattern_slp"), year)
    t850_data = ds_850[cfg.var_t850].values
    slp_data = ds_slp[cfg.var_msl].values

    lon2d, lat2d = load_grid(ds_slp)
    times = load_times(ds_slp)

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout_tc = pathlib.Path(cfg.data_tracking) / f"tc_{year:04d}.nc"

    TC_tracking(
        cy_slp_objects,
        t850_data,
        slp_data,
        lon2d,
        lat2d,
        times=times,
        nc_file=str(fileout_tc),
    )

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


###############################################################################
if __name__ == "__main__":
    main()
