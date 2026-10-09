"""``atmotrack-col`` — Cut-Off Low tracking from any CF-compliant dataset.

For each year in the configured input dataset:
  1. Loads 500, 200, 850 hPa geopotential/wind/temperature and precipitation,
     all brought to the configured time step ``DT`` (precipitation accumulated).
  2. Runs CY_ACY_z500_tracking to identify upper-level cyclones.
  3. Runs COL_tracking to classify cut-off lows within a configured region.
  4. Writes ``col_z500_{year}.nc`` to ``data_tracking``.

All parameters (thresholds, domain, paths, variable names) are read from
``config.toml``.  Set ``[data_source]`` keys to switch between ERA5, WRF, etc.
"""

import pathlib
import time

from atmotrack import config as cfg
from atmotrack.cli._common import (
    check_same_grid,
    check_same_times,
    load_precip_stream,
    load_stream,
    parse_args,
    run_years,
    worker_logger,
    years_in_pattern,
)
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

    z500 = load_stream("pattern_z500", year, logger)
    z200 = load_stream("pattern_z200", year, logger)
    z850 = load_stream("pattern_z850", year, logger)
    pr = load_precip_stream("pattern_pr", year, logger)
    for other, name in ((z200, "z200"), (z850, "z850")):
        check_same_grid(z500, other, name)
        check_same_times(z500, other, name)
    check_same_grid(z500, pr, "pr")

    z500_data = z500.field("var_z500")
    u200_data = z200.field("var_u200")
    t850_data = z850.field("var_t850")
    u850_data = z850.field("var_u850")
    v850_data = z850.field("var_v850")
    # precipitation -> mm per DT window (units from the file, e.g. ERA5 tp in m),
    # placed on the z500 time axis
    pr_data, pr_data_max = pr.accumulated("var_pr", "tp", target_times=z500.times)
    if pr_data.shape[0] != z500_data.shape[0]:
        raise ValueError(
            f"precipitation has {pr_data.shape[0]} windows but z500 has "
            f"{z500_data.shape[0]} steps after alignment"
        )

    lon2d, lat2d, times = z500.lon, z500.lat, z500.times

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
