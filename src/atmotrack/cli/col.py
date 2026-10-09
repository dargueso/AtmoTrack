"""``atmotrack-col`` — Cut-Off Low tracking from any CF-compliant dataset.

For each year in the configured input dataset:
  1. Loads 500, 200, 850 hPa geopotential/wind/temperature and precipitation.
  2. Runs CY_ACY_z500_tracking to identify upper-level cyclones.
  3. Runs COL_tracking to classify cut-off lows within a configured region.
  4. Writes ``col_z500_{year}.nc`` to ``data_tracking``.

All parameters (thresholds, domain, paths, variable names) are read from
``config.toml``.  Set ``[data_source]`` keys to switch between ERA5, WRF, etc.
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
from atmotrack.tracking import COL_tracking, CY_ACY_z500_tracking


def main(argv: list[str] | None = None) -> None:
    """Loop over available years and track COLs in parallel."""
    args = parse_args("Cut-Off Low tracking from 500 hPa geopotential.", argv)
    run_years(cutofflow_tracking, years_in_pattern("pattern_z500", args), args)


def cutofflow_tracking(year: int, verbose: bool = False) -> None:
    """Track COLs for a single year."""
    logger = worker_logger(verbose)
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
    warn_if_dt_differs(logger, infer_dt(times))

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


if __name__ == "__main__":
    main()
