#!/usr/bin/env python
"""
SLP_tracking_ERA5.py — Surface Cyclone / Anticyclone tracking from SLP.

For each year in the configured input dataset:
  1. Loads mean sea-level pressure (msl, Pa).
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
from tracking_functions import CY_ACY_slp_tracking
from utils import get_logger


###########################################################
def main():
    """Loop over available years and track surface CY/ACY in parallel."""
    parser = argparse.ArgumentParser(description="Surface CY/ACY tracking from SLP.")
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

    ds_slp = open_pattern("pattern_slp")
    all_years = available_years(ds_slp)
    years = [y for y in all_years if args.year_start <= y <= args.year_end]
    ds_slp.close()

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
    Parallel(n_jobs=n_jobs)(delayed(slp_tracking)(year, args.verbose) for year in years)


###########################################################
def slp_tracking(year: int, verbose: bool = False) -> None:
    """Track surface cyclones/anticyclones for a single year."""
    logger = get_logger("atmotrack", level=logging.DEBUG if verbose else logging.INFO)
    logger.info(f"Analyzing year {year}")
    start_time = time.time()

    ds = slice_year(open_pattern("pattern_slp"), year)

    # msl is in Pa; conversion to hPa happens inside CY_ACY_slp_tracking
    slp_data = ds[cfg.var_msl].values

    lon2d, lat2d = load_grid(ds)
    times = load_times(ds)
    _dt_data = infer_dt(times)
    if _dt_data != cfg.DT:
        logger.warning(
            f"Data timestep ({_dt_data} h) differs from config DT ({cfg.DT} h). "
            "Tracking thresholds use cfg.DT — update [general] DT in config.toml if needed."
        )

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")

    fileout = pathlib.Path(cfg.data_tracking) / f"cy_slp_{year:04d}.nc"

    _, _ = CY_ACY_slp_tracking(slp_data, times, lon2d, lat2d, nc_file=str(fileout))

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


###############################################################################
if __name__ == "__main__":
    main()
