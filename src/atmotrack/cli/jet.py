"""``atmotrack-jet`` — Jet stream tracking from 200 hPa winds.

For each year in the configured input dataset:
  1. Loads u and v wind components at 200 hPa.
  2. Computes wind speed magnitude (uv200 = sqrt(u² + v²)).
  3. Runs jetstream_tracking to label jet stream objects.
  4. Writes ``jet_{year}.nc`` to ``data_tracking``.

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
from atmotrack.tracking import jetstream_tracking


def main(argv: list[str] | None = None) -> None:
    """Loop over available years and track jet streams in parallel."""
    args = parse_args("Jet stream tracking from 200 hPa winds.", argv)
    run_years(jet_tracking_worker, years_in_pattern("pattern_z200", args), args)


def jet_tracking_worker(year: int, verbose: bool = False) -> None:
    """Track jet stream for a single year."""
    logger = worker_logger(verbose)
    logger.info(f"Analyzing year {year}")
    start_time = time.time()

    ds = slice_year(open_pattern("pattern_z200"), year)

    u200 = ds[cfg.var_u200].values
    v200 = ds[cfg.var_v200].values
    uv200 = np.sqrt(u200**2 + v200**2)

    lon2d, lat2d = load_grid(ds)
    times = load_times(ds)
    warn_if_dt_differs(logger, infer_dt(times))

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout = pathlib.Path(cfg.data_tracking) / f"jet_{year:04d}.nc"

    jetstream_tracking(uv200, times, lon2d, lat2d, nc_file=str(fileout))

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


if __name__ == "__main__":
    main()
