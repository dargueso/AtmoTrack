#!/usr/bin/env python
"""
JetStream_tracking_ERA5.py — Jet stream tracking from ERA5 200 hPa winds.

For each annual ERA5 200 hPa file in ``data_era5/``:
  1. Loads u and v wind components at 200 hPa.
  2. Computes wind speed magnitude (uv200 = sqrt(u² + v²)).
  3. Runs jetstream_tracking to label jet stream objects.
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
from tracking_functions import jetstream_tracking
from utils import get_logger


###########################################################
def main():
    """Loop over available annual 200 hPa files and track jet streams in parallel."""
    parser = argparse.ArgumentParser(description="Jet stream tracking from ERA5 200 hPa winds.")
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
        for f in glob(f"{cfg.data_era5}/era5_daily_200hPa_????.nc")
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
    Parallel(n_jobs=n_jobs)(delayed(jet_tracking_worker)(fin, args.verbose) for fin in filesin)


###########################################################
def jet_tracking_worker(fin_200hpa, verbose=False):
    """Track jet stream for a single annual file."""
    logger = get_logger("atmotrack", level=logging.DEBUG if verbose else logging.INFO)
    logger.info(f"Analyzing {fin_200hpa}")
    start_time = time.time()

    ds = xr.open_dataset(fin_200hpa).squeeze()

    u200 = ds.u.values
    v200 = ds.v.values
    uv200 = np.sqrt(u200**2 + v200**2)

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

    fileout = fin_200hpa.replace("200hPa", "jet").replace(cfg.data_era5, cfg.data_tracking)

    jetstream_tracking(uv200, times, lon2d, lat2d, nc_file=fileout)

    logger.info(f"DONE {fin_200hpa} in {time.time() - start_time:.2f} s")


###############################################################################
if __name__ == "__main__":
    main()
