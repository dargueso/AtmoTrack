"""``atmotrack-ar-850`` — Atmospheric River tracking from 850 hPa moisture flux.

For each year in the configured input dataset:
  1. Loads u, v wind and q specific humidity at 850 hPa.
  2. Computes moisture flux magnitude: sqrt((u·q)² + (v·q)²).
  3. Runs AR_850hPa_tracking to label AR objects.
  4. Writes ``ar850_{year}.nc`` to ``data_tracking``.

All parameters (thresholds, paths, variable names) are read from ``config.toml``.
Set ``[data_source]`` keys to switch between ERA5, WRF, etc.
"""

import pathlib
import time

import numpy as np

from atmotrack import config as cfg
from atmotrack.cli._common import (
    parse_args,
    run_years,
    warn_if_dt_differs,
    worker_logger,
    years_in_pattern,
)
from atmotrack.io import infer_dt, load_grid, load_times, open_pattern, slice_year
from atmotrack.tracking import AR_850hPa_tracking


def main(argv: list[str] | None = None) -> None:
    """Loop over available years and track ARs in parallel."""
    args = parse_args("Atmospheric river tracking from 850 hPa moisture flux.", argv)
    run_years(ar850_tracking_worker, years_in_pattern("pattern_z850", args), args)


def ar850_tracking_worker(year: int, verbose: bool = False) -> None:
    """Track 850 hPa ARs for a single year."""
    logger = worker_logger(verbose)
    logger.info(f"Analyzing year {year}")
    start_time = time.time()

    ds = slice_year(open_pattern("pattern_z850"), year)

    u850 = ds[cfg.var_u850].values
    v850 = ds[cfg.var_v850].values
    q850 = ds[cfg.var_q850].values  # specific humidity [kg/kg]; treated as g/g for magnitude

    # 850 hPa moisture flux magnitude
    VapTrans = np.sqrt((u850 * q850) ** 2 + (v850 * q850) ** 2)

    lon2d, lat2d = load_grid(ds)
    times = load_times(ds)
    warn_if_dt_differs(logger, infer_dt(times))

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout = pathlib.Path(cfg.data_tracking) / f"ar850_{year:04d}.nc"

    AR_850hPa_tracking(VapTrans, times, lon2d, lat2d, nc_file=str(fileout))

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


if __name__ == "__main__":
    main()
