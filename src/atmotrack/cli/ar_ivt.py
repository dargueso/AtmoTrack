"""``atmotrack-ar-ivt`` — Atmospheric River tracking from Integrated Vapour Transport.

For each year in the configured input dataset:
  1. Loads eastward (ivte) and northward (ivtn) IVT components [kg m⁻¹ s⁻¹].
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
    parse_args,
    run_years,
    warn_if_dt_differs,
    worker_logger,
    years_in_pattern,
)
from atmotrack.io import infer_dt, load_grid, load_times, open_pattern, slice_year
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

    ds = slice_year(open_pattern("pattern_ivt"), year)

    ivte = ds[cfg.var_ivte].values  # eastward IVT [kg m⁻¹ s⁻¹]
    ivtn = ds[cfg.var_ivtn].values  # northward IVT [kg m⁻¹ s⁻¹]
    IVT = np.sqrt(ivte**2 + ivtn**2)

    lon2d, lat2d = load_grid(ds)
    times = load_times(ds)
    warn_if_dt_differs(logger, infer_dt(times))

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout = pathlib.Path(cfg.data_tracking) / f"ar_ivt_{year:04d}.nc"

    AR_IVT_tracking(IVT, times, lon2d, lat2d, nc_file=str(fileout))

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


if __name__ == "__main__":
    main()
