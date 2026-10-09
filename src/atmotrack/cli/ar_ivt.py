"""``atmotrack-ar-ivt`` — Atmospheric River tracking from Integrated Vapour Transport.

For each year in the configured input dataset:
  1. Loads eastward (ivte) and northward (ivtn) IVT components [kg m⁻¹ s⁻¹]
     at the configured time step ``DT``.
  2. Computes IVT magnitude: sqrt(ivte² + ivtn²).
  3. Runs AR_IVT_tracking to label AR objects.
  4. Writes ``ar_ivt_{year}.nc`` to ``data_tracking``.

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
from atmotrack.tracking import AR_IVT_tracking


def main(argv: list[str] | None = None) -> None:
    """Loop over available years and track ARs in parallel."""
    args = parse_args("Atmospheric river tracking from integrated vapour transport.", argv)
    run_years(ar_ivt_tracking_worker, years_in_pattern("pattern_ivt", args), args)


def ar_ivt_tracking_worker(year: int, verbose: bool = False) -> None:
    """Track IVT ARs for a single year."""
    logger = worker_logger(verbose)
    logger.info(f"Analyzing year {year}")
    start_time = time.time()

    ivt = load_stream("pattern_ivt", year, logger)
    ivte = ivt.field("var_ivte")  # eastward IVT [kg m⁻¹ s⁻¹]
    ivtn = ivt.field("var_ivtn")  # northward IVT [kg m⁻¹ s⁻¹]
    IVT = np.sqrt(ivte**2 + ivtn**2)

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout = pathlib.Path(cfg.data_tracking) / f"ar_ivt_{year:04d}.nc"

    AR_IVT_tracking(IVT, ivt.times, ivt.lon, ivt.lat, nc_file=str(fileout))

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


if __name__ == "__main__":
    main()
