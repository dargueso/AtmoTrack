"""``atmotrack-prepare`` — write AtmoTrack's canonical per-year input files.

Reads the source through ``[data_source]`` and ``[prepare]`` in ``config.toml``
and writes ``atmotrack_<stream>_<year>.nc`` files (ERA5 variable names and
units, all streams at ``DT``, requested longitude convention) to
``[prepare] output_dir``.  Afterwards point ``data_input`` at that directory
and use the ``[data_source]`` block printed at the end (or ``--write-config``).
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import time

from joblib import Parallel, delayed

from atmotrack import config as cfg
from atmotrack.cli._common import LOG_FILE, limit_blas_threads, select_years, worker_logger
from atmotrack.io import available_years, index_pattern, load_grid
from atmotrack.prepare import (
    STREAMS,
    canonical_data_source,
    configured_streams,
    output_dir,
    prepare_year,
    write_prepared_config,
)
from atmotrack.utils import get_logger


def _worker(year: int, streams: list[str], force: bool, verbose: bool) -> dict:
    logger = worker_logger(verbose)
    t0 = time.time()
    out = prepare_year(year, streams=streams, force=force)
    logger.info(f"prepared {year} ({len(out)} stream(s)) in {time.time() - t0:.1f} s")
    return {k: str(v) for k, v in out.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--year-start", type=int, metavar="YEAR", help="first year (default: all)")
    parser.add_argument("--year-end", type=int, metavar="YEAR", help="last year (default: all)")
    parser.add_argument(
        "--years", type=int, nargs="+", metavar="YEAR", help="explicit list of years"
    )
    parser.add_argument("--current-year", action="store_true", help="only the current year")
    parser.add_argument(
        "--streams",
        nargs="+",
        choices=list(STREAMS),
        metavar="STREAM",
        help=f"streams to prepare (default: all configured; choices: {', '.join(STREAMS)})",
    )
    parser.add_argument("--output-dir", type=pathlib.Path, help="override [prepare] output_dir")
    parser.add_argument("--force", action="store_true", help="rewrite existing output files")
    parser.add_argument(
        "--jobs", "-j", type=int, default=4, metavar="N", help="parallel years (default: 4)"
    )
    parser.add_argument(
        "--write-config",
        type=pathlib.Path,
        metavar="PATH",
        help="write a copy of the active config that reads the prepared files",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logger = get_logger(
        "atmotrack", log_file=LOG_FILE, level=logging.DEBUG if args.verbose else logging.INFO
    )
    if args.output_dir:
        cfg.output_dir = str(args.output_dir)  # type: ignore[attr-defined]

    streams = args.streams or configured_streams()
    if not streams:
        parser.error("no pattern_* keys configured in [data_source]")

    if args.years:
        years = sorted(args.years)
    else:
        all_years: set[int] = set()
        for s in streams:
            try:
                all_years.update(available_years(f"pattern_{s}"))
            except FileNotFoundError as exc:
                logger.warning(str(exc))
        if args.current_year:
            args.year_start = args.year_end = time.localtime().tm_year
        if args.year_start is None:
            args.year_start = min(all_years) if all_years else 0
        if args.year_end is None:
            args.year_end = max(all_years) if all_years else -1
        years = select_years(all_years, args)
    if not years:
        return 1

    logger.info(
        f"Preparing {len(years)} year(s) for streams {streams} into {output_dir().resolve()}"
    )
    limit_blas_threads()
    n_jobs = max(1, min(len(years), args.jobs))
    Parallel(n_jobs=n_jobs)(delayed(_worker)(y, streams, args.force, args.verbose) for y in years)

    # Report the [data_source] block for the prepared layout
    regular = True
    try:
        first = index_pattern(f"pattern_{streams[0]}")[0].path
        import xarray as xr

        with xr.open_dataset(first) as ds:
            lon, lat = load_grid(ds)
        from atmotrack.grid import is_regular

        regular = is_regular(lon, lat)
    except Exception:  # noqa: BLE001
        pass
    print("\nPrepared files written to", output_dir().resolve())
    print("Use this configuration to read them:\n")
    print("[paths]")
    print(f'data_input = "{output_dir().resolve()}"\n')
    print(canonical_data_source(regular))
    if args.write_config:
        write_prepared_config(args.write_config, regular)
        print(f"Config written to {args.write_config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
