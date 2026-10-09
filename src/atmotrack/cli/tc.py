"""``atmotrack-tc`` — Tropical Cyclone tracking.

Reads the surface cyclone objects produced by ``atmotrack-cy-slp``, then
loads 850 hPa temperature and SLP from the configured input dataset to apply
the tropical cyclone criteria.

For each year with an existing ``cy_slp_{year}.nc`` file:
  1. Loads the pre-computed SLP cyclone objects.
  2. Loads 850 hPa temperature and SLP from the configured input source.
  3. Runs TC_tracking to filter confirmed tropical cyclones.
  4. Writes ``tc_{year}.nc`` to ``data_tracking``.

All parameters (thresholds, paths, variable names) are read from ``config.toml``.
Set ``[data_source]`` keys to switch between ERA5, WRF, etc.

Note: run ``atmotrack-cy-slp`` first to generate the required cy_slp files.
"""

import pathlib
import time

import xarray as xr

from atmotrack import config as cfg
from atmotrack.cli._common import LOGGER_NAME, parse_args, run_years, worker_logger
from atmotrack.io import load_grid, load_times, open_pattern, slice_year
from atmotrack.tracking import TC_tracking
from atmotrack.utils import get_logger


def main(argv: list[str] | None = None) -> None:
    """Loop over available cy_slp files and track TCs in parallel."""
    args = parse_args("Tropical cyclone tracking (requires atmotrack-cy-slp output).", argv)

    # Discover years from the existing cy_slp output files
    cy_slp_dir = pathlib.Path(cfg.data_tracking)
    cy_slp_files = sorted(cy_slp_dir.glob("cy_slp_????.nc"))
    all_years = [int(f.stem[-4:]) for f in cy_slp_files]
    years = [y for y in all_years if args.year_start <= y <= args.year_end]

    if not years:
        logger = get_logger(LOGGER_NAME)
        if all_years:
            logger.warning(
                f"No cy_slp files found for {args.year_start}–{args.year_end}. "
                f"Available years: {all_years}"
            )
        else:
            logger.warning(f"No cy_slp files found in {cy_slp_dir}. Run atmotrack-cy-slp first.")
        return

    run_years(tc_tracking_worker, years, args)


def tc_tracking_worker(year: int, verbose: bool = False) -> None:
    """Track tropical cyclones for a single year."""
    logger = worker_logger(verbose)
    logger.info(f"Analyzing year {year}")
    start_time = time.time()

    # Load pre-computed SLP cyclone objects (internal tracking output)
    cy_slp_path = pathlib.Path(cfg.data_tracking) / f"cy_slp_{year:04d}.nc"
    ds_cy = xr.open_dataset(cy_slp_path).squeeze()
    cy_slp_objects = ds_cy.cy_slp_objects.values.astype(int)

    # Load 850 hPa temperature and SLP from the configured input source
    ds_850 = slice_year(open_pattern("pattern_z850"), year)
    ds_slp = slice_year(open_pattern("pattern_slp"), year)
    t850_data = ds_850[cfg.var_t850].values
    slp_data = ds_slp[cfg.var_msl].values

    lon2d, lat2d = load_grid(ds_slp)
    times = load_times(ds_slp)

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout_tc = pathlib.Path(cfg.data_tracking) / f"tc_{year:04d}.nc"

    TC_tracking(
        cy_slp_objects,
        t850_data,
        slp_data,
        lon2d,
        lat2d,
        times=times,
        nc_file=str(fileout_tc),
    )

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


if __name__ == "__main__":
    main()
