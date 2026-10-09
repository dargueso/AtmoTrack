"""``atmotrack-cy-z500`` — 500 hPa Cyclone / Anticyclone tracking.

For each year in the configured input dataset:
  1. Loads geopotential at 500 hPa.
  2. Computes a smoothed anomaly and labels cyclonic / anticyclonic objects.
  3. Writes ``cy_z500_{year}.nc`` to ``data_tracking``.

All parameters (thresholds, paths, variable names) are read from ``config.toml``.
Set ``[data_source]`` keys to switch between ERA5, WRF, etc.
"""

import pathlib
import time

from atmotrack import config as cfg
from atmotrack.cli._common import (
    parse_args,
    run_years,
    warn_if_dt_differs,
    worker_logger,
    years_in_pattern,
)
from atmotrack.io import infer_dt, load_grid, load_times, open_pattern, slice_year
from atmotrack.tracking import CY_ACY_z500_tracking


def main(argv: list[str] | None = None) -> None:
    """Loop over available years and track upper-level CY/ACY in parallel."""
    args = parse_args("500 hPa cyclone / anticyclone tracking.", argv)
    run_years(cy_z500_tracking, years_in_pattern("pattern_z500", args), args)


def cy_z500_tracking(year: int, verbose: bool = False) -> None:
    """Track 500 hPa cyclones/anticyclones for a single year."""
    logger = worker_logger(verbose)
    logger.info(f"Analyzing year {year}")
    start_time = time.time()

    ds = slice_year(open_pattern("pattern_z500"), year)
    z500_data = ds[cfg.var_z500].values

    lon2d, lat2d = load_grid(ds)
    times = load_times(ds)
    warn_if_dt_differs(logger, infer_dt(times))

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")

    fileout = pathlib.Path(cfg.data_tracking) / f"cy_z500_{year:04d}.nc"

    _, _ = CY_ACY_z500_tracking(z500_data, times, lon2d, lat2d, nc_file=str(fileout))

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


if __name__ == "__main__":
    main()
