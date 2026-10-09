"""``atmotrack-cy-slp`` — Surface Cyclone / Anticyclone tracking from SLP.

For each year in the configured input dataset:
  1. Loads mean sea-level pressure (msl, Pa) at the configured time step ``DT``.
  2. Computes a smoothed anomaly and labels cyclonic / anticyclonic objects.
  3. Writes ``cy_slp_{year}.nc`` to ``data_tracking``.

All parameters (thresholds, paths, variable names) are read from ``config.toml``.
Set ``[data_source]`` keys to switch between ERA5, WRF, etc.
"""

import pathlib
import time

from atmotrack import config as cfg
from atmotrack.cli._common import (
    load_stream,
    parse_args,
    run_years,
    worker_logger,
    years_in_pattern,
)
from atmotrack.tracking import CY_ACY_slp_tracking


def main(argv: list[str] | None = None) -> None:
    """Loop over available years and track surface CY/ACY in parallel."""
    args = parse_args("Surface cyclone / anticyclone tracking from SLP.", argv)
    run_years(slp_tracking, years_in_pattern("pattern_slp", args), args)


def slp_tracking(year: int, verbose: bool = False) -> None:
    """Track surface cyclones/anticyclones for a single year."""
    logger = worker_logger(verbose)
    logger.info(f"Analyzing year {year}")
    start_time = time.time()

    slp = load_stream("pattern_slp", year, logger)
    # msl is in Pa; conversion to hPa happens inside CY_ACY_slp_tracking
    slp_data = slp.field("var_msl")

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")

    fileout = pathlib.Path(cfg.data_tracking) / f"cy_slp_{year:04d}.nc"

    _, _ = CY_ACY_slp_tracking(slp_data, slp.times, slp.lon, slp.lat, nc_file=str(fileout))

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


if __name__ == "__main__":
    main()
