"""Shared plumbing for the ``atmotrack-*`` tracking commands.

Every tracking command processes one calendar year per worker, in parallel
over the years found in the input data.  This module holds the argument
parser, the logger set-up, the BLAS thread limits and the year discovery that
all of them share.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from collections.abc import Callable, Iterable

from joblib import Parallel, delayed

from atmotrack import config as cfg
from atmotrack.io import available_years, open_pattern
from atmotrack.utils import get_logger

LOG_FILE = "out.log"
LOGGER_NAME = "atmotrack"


def build_parser(description: str) -> argparse.ArgumentParser:
    """Parser with the flags common to all tracking commands."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--year-start",
        type=int,
        default=1940,
        metavar="YEAR",
        help="First year to process (default: 1940)",
    )
    parser.add_argument(
        "--year-end",
        type=int,
        default=2024,
        metavar="YEAR",
        help="Last year to process (default: 2024)",
    )
    parser.add_argument(
        "--current-year",
        action="store_true",
        help="Process the current (possibly incomplete) year; overrides --year-start/--year-end",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG-level logging")
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=8,
        metavar="N",
        help="Number of parallel workers (default: 8)",
    )
    return parser


def parse_args(description: str, argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the common flags, apply ``--current-year`` and set up logging."""
    args = build_parser(description).parse_args(argv)
    if args.current_year:
        args.year_start = args.year_end = time.localtime().tm_year
    get_logger(
        LOGGER_NAME,
        log_file=LOG_FILE,
        level=logging.DEBUG if args.verbose else logging.INFO,
    )
    get_logger(LOGGER_NAME).debug(f"Using configuration file {cfg.config_path}")
    os.makedirs(cfg.data_tracking, exist_ok=True)
    return args


def select_years(all_years: Iterable[int], args: argparse.Namespace) -> list[int]:
    """Years from *all_years* inside the requested range; warns when empty."""
    all_years = sorted(all_years)
    years = [y for y in all_years if args.year_start <= y <= args.year_end]
    if not years:
        get_logger(LOGGER_NAME).warning(
            f"No data found for {args.year_start}–{args.year_end}. "
            f"Available years in input files: {all_years}"
        )
    return years


def years_in_pattern(pattern_key: str, args: argparse.Namespace) -> list[int]:
    """Years available in the files matching ``cfg.<pattern_key>``, within the range."""
    ds = open_pattern(pattern_key)
    try:
        all_years = available_years(ds)
    finally:
        ds.close()
    return select_years(all_years, args)


def limit_blas_threads() -> None:
    """One BLAS thread per worker: avoids contention when running many years at once."""
    for var in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[var] = "1"


def run_years(
    worker: Callable[[int, bool], None], years: list[int], args: argparse.Namespace
) -> None:
    """Run ``worker(year, verbose)`` for every year in parallel."""
    if not years:
        return
    limit_blas_threads()
    n_jobs = min(len(years), args.jobs)
    Parallel(n_jobs=n_jobs)(delayed(worker)(year, args.verbose) for year in years)


def worker_logger(verbose: bool) -> logging.Logger:
    """Logger for a worker process (joblib workers do not inherit handlers)."""
    return get_logger(LOGGER_NAME, level=logging.DEBUG if verbose else logging.INFO)


def warn_if_dt_differs(logger: logging.Logger, dt_data: int) -> None:
    """Warn when the data timestep differs from ``[general] DT`` in the config."""
    if dt_data != cfg.DT:
        logger.warning(
            f"Data timestep ({dt_data} h) differs from config DT ({cfg.DT} h). "
            "Tracking thresholds use cfg.DT — update [general] DT in config.toml if needed."
        )
