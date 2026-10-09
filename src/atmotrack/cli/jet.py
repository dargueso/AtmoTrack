"""``atmotrack-jet`` — Jet stream tracking from 200 hPa winds.

For each year in the configured input dataset:
  1. Loads u and v wind components at 200 hPa (at the configured time step ``DT``).
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
    load_stream,
    parse_args,
    run_years,
    worker_logger,
    years_in_pattern,
)
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

    z200 = load_stream("pattern_z200", year, logger)
    u200 = z200.field("var_u200")
    v200 = z200.field("var_v200")
    uv200 = np.sqrt(u200**2 + v200**2)

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout = pathlib.Path(cfg.data_tracking) / f"jet_{year:04d}.nc"

    jetstream_tracking(uv200, z200.times, z200.lon, z200.lat, nc_file=str(fileout))

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


if __name__ == "__main__":
    main()
