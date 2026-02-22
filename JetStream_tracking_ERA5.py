#!/usr/bin/env python
"""
JetStream_tracking_ERA5.py — Jet stream tracking from 200 hPa winds.

For each year in the configured input dataset:
  1. Loads u and v wind components at 200 hPa.
  2. Computes wind speed magnitude (uv200 = sqrt(u² + v²)).
  3. Runs jetstream_tracking to label jet stream objects.
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
from atmotrack_io import available_years, infer_dt, load_grid, load_times, open_pattern, slice_year
from tracking_functions import jetstream_tracking
from utils import get_logger


###########################################################
def main():
    """Loop over available years and track jet streams in parallel."""
    parser = argparse.ArgumentParser(description="Jet stream tracking from 200 hPa winds.")
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
    parser.add_argument(
        "--current-year",
        action="store_true",
        help="Process the current (possibly incomplete) year; overrides --year-start/--year-end",
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
    if args.current_year:
        args.year_start = args.year_end = time.localtime().tm_year

    get_logger(
        "atmotrack", log_file="out.log", level=logging.DEBUG if args.verbose else logging.INFO
    )
    os.makedirs(cfg.data_tracking, exist_ok=True)

    ds_z200 = open_pattern("pattern_z200")
    all_years = available_years(ds_z200)
    years = [y for y in all_years if args.year_start <= y <= args.year_end]
    ds_z200.close()

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
    Parallel(n_jobs=n_jobs)(delayed(jet_tracking_worker)(year, args.verbose) for year in years)


###########################################################
def jet_tracking_worker(year: int, verbose: bool = False) -> None:
    """Track jet stream for a single year."""
    logger = get_logger("atmotrack", level=logging.DEBUG if verbose else logging.INFO)
    logger.info(f"Analyzing year {year}")
    start_time = time.time()

    ds = slice_year(open_pattern("pattern_z200"), year)

    u200 = ds[cfg.var_u200].values
    v200 = ds[cfg.var_v200].values
    uv200 = np.sqrt(u200**2 + v200**2)

    lon2d, lat2d = load_grid(ds)
    times = load_times(ds)
    _dt_data = infer_dt(times)
    if _dt_data != cfg.DT:
        logger.warning(
            f"Data timestep ({_dt_data} h) differs from config DT ({cfg.DT} h). "
            "Tracking thresholds use cfg.DT — update [general] DT in config.toml if needed."
        )

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout = pathlib.Path(cfg.data_tracking) / f"jet_{year:04d}.nc"

    jetstream_tracking(uv200, times, lon2d, lat2d, nc_file=str(fileout))

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


###############################################################################
if __name__ == "__main__":
    main()
