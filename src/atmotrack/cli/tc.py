"""``atmotrack-tc`` — Tropical Cyclone tracking.

Reads the surface cyclone objects produced by ``atmotrack-cy-slp``, then
loads 850 hPa temperature and SLP from the configured input dataset (both at
the configured time step ``DT``) to apply the tropical cyclone criteria.

For each year with an existing ``cy_slp_{year}.nc`` file:
  1. Loads the pre-computed SLP cyclone objects.
  2. Loads 850 hPa temperature and SLP from the configured input source.
  3. Runs TC_tracking to filter confirmed tropical cyclones.
  4. Writes ``tc_{year}.nc`` to ``data_tracking``.

Note: run ``atmotrack-cy-slp`` first to generate the required cy_slp files.
"""

import pathlib
import time

import xarray as xr

from atmotrack import config as cfg
from atmotrack.cli._common import (
    LOGGER_NAME,
    check_same_grid,
    check_same_times,
    load_stream,
    parse_args,
    run_years,
    worker_logger,
)
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
    with xr.open_dataset(cy_slp_path) as ds_cy:
        cy_slp_objects = ds_cy["cy_slp_objects"].values.astype(int)

    # Load 850 hPa temperature and SLP on the same DT time axis
    z850 = load_stream("pattern_z850", year, logger)
    slp = load_stream("pattern_slp", year, logger)
    check_same_grid(slp, z850, "t850")
    check_same_times(slp, z850, "t850")
    t850_data = z850.field("var_t850")
    slp_data = slp.field("var_msl")

    if cy_slp_objects.shape != slp_data.shape:
        raise ValueError(
            f"cy_slp_{year}.nc has shape {cy_slp_objects.shape} but the SLP input at DT="
            f"{cfg.DT:g} h has {slp_data.shape}; re-run atmotrack-cy-slp with the current config"
        )

    logger.debug(f"Loading data: {time.time() - start_time:.2f} s")
    start_time = time.time()

    fileout_tc = pathlib.Path(cfg.data_tracking) / f"tc_{year:04d}.nc"

    TC_tracking(
        cy_slp_objects,
        t850_data,
        slp_data,
        slp.lon,
        slp.lat,
        times=slp.times,
        nc_file=str(fileout_tc),
    )

    logger.info(f"DONE year {year} in {time.time() - start_time:.2f} s")


if __name__ == "__main__":
    main()
