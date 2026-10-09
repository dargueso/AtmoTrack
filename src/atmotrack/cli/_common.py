"""Shared plumbing for the ``atmotrack-*`` tracking commands.

Every tracking command processes one calendar year per worker, in parallel
over the years found in the input data.  This module holds the argument
parser, the logger set-up, the BLAS thread limits, the year discovery and the
stream loader that brings every input to the configured time step ``DT``.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np
import xarray as xr
from joblib import Parallel, delayed

from atmotrack import config as cfg
from atmotrack import timeaxis
from atmotrack.io import (
    accumulate_to_dt,
    available_years,
    ensure_tyx,
    load_grid,
    open_years,
    spatial_dims,
    subsample_to_dt,
)
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
    t0 = time.time()
    all_years = available_years(pattern_key)
    get_logger(LOGGER_NAME).debug(f"Indexed {pattern_key} in {time.time() - t0:.1f} s")
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


# ---------------------------------------------------------------------------
# Stream loading at the configured time step
# ---------------------------------------------------------------------------


@dataclass
class Stream:
    """One input stream for one year, aligned to ``cfg.DT``."""

    ds: xr.Dataset
    times: object  # DatetimeIndex or CFTimeIndex
    lon: np.ndarray
    lat: np.ndarray
    ydim: str
    xdim: str

    def field(self, var_key: str) -> np.ndarray:
        """Instantaneous variable ``cfg.<var_key>`` as ``(time, y, x)`` at DT."""
        name = getattr(cfg, var_key)
        if name not in self.ds:
            raise KeyError(
                f"variable '{name}' ({var_key}) not found in the input files; "
                f"available: {list(self.ds.data_vars)}"
            )
        da = ensure_tyx(self.ds[name], self.ydim, self.xdim)
        da = subsample_to_dt(da, cfg.DT)
        values = np.asarray(da.values)
        if values.shape[0] != len(self.times):
            raise ValueError(
                f"'{name}': {values.shape[0]} steps after alignment, expected {len(self.times)}"
            )
        return values

    def accumulated(self, var_key: str, canon: str = "tp", target_times=None):
        """Accumulated variable at DT in canonical units: ``(total, peak)`` arrays.

        The source units come from the file's ``units`` attribute (or the
        ``units_<canon>`` config override) and are converted with the same table
        as ``atmotrack-prepare`` (ERA5 ``tp`` in m -> mm; rates -> amounts per
        step).  Values on a finer step are summed over each DT window (peak = the
        largest per-input-step value in the window) and the windows are placed on
        *target_times* (the DT axis of the instantaneous streams; default: this
        stream's own axis after accumulation).  Missing windows are zero.
        """
        from atmotrack.prepare import convert_units, precipitation_kind

        name = getattr(cfg, var_key)
        if name not in self.ds:
            raise KeyError(
                f"variable '{name}' ({var_key}) not found in the input files; "
                f"available: {list(self.ds.data_vars)}"
            )
        da = ensure_tyx(self.ds[name], self.ydim, self.xdim)
        da, kind = convert_units(da, canon)
        total, peak = accumulate_to_dt(da, cfg.DT, kind=precipitation_kind(kind))
        ref = total[cfg.time_var] if target_times is None else target_times
        target = np.asarray(ref.values if hasattr(ref, "values") else ref)
        total = total.reindex({cfg.time_var: target}, fill_value=0.0)
        peak = peak.reindex({cfg.time_var: target}, fill_value=0.0)
        return np.asarray(total.values), np.asarray(peak.values)


def load_stream(pattern_key: str, year: int, logger: logging.Logger | None = None) -> Stream:
    """Open the files of *pattern_key* for *year* and align the time axis to ``cfg.DT``.

    Instantaneous streams at a finer step than DT are subsampled; a step that
    does not divide DT is an error (run ``atmotrack-check-input``).
    """
    logger = logger or get_logger(LOGGER_NAME)
    t0 = time.time()
    ds = open_years(pattern_key, year)
    times = timeaxis.load_times(ds, cfg.time_var)
    step = timeaxis.step_hours(times)
    if not np.isnan(step) and not np.isclose(step, cfg.DT):
        ratio = cfg.DT / step
        if ratio < 1 or not np.isclose(ratio, round(ratio)):
            raise ValueError(
                f"{pattern_key}: data step {step:g} h is incompatible with DT={cfg.DT:g} h "
                "(must be equal or a divisor). Run atmotrack-check-input, or prepare the data "
                "with atmotrack-prepare, or change [general] DT."
            )
        mask = timeaxis.on_step_mask(times, cfg.DT)
        ds = ds.isel({cfg.time_var: np.where(mask)[0]})
        times = timeaxis.load_times(ds, cfg.time_var)
        logger.debug(f"{pattern_key}: subsampled {step:g} h -> {cfg.DT:g} h")
    lon, lat = load_grid(ds)
    ydim, xdim = spatial_dims(ds)
    logger.debug(f"{pattern_key} {year}: {timeaxis.describe(times)} ({time.time() - t0:.1f} s)")
    return Stream(ds=ds, times=times, lon=lon, lat=lat, ydim=ydim, xdim=xdim)


def load_precip_stream(pattern_key: str, year: int, logger: logging.Logger | None = None):
    """Open a precipitation stream for *year* without subsampling (accumulated later)."""
    logger = logger or get_logger(LOGGER_NAME)
    ds = open_years(pattern_key, year)
    times = timeaxis.load_times(ds, cfg.time_var)
    lon, lat = load_grid(ds)
    ydim, xdim = spatial_dims(ds)
    logger.debug(f"{pattern_key} {year}: {timeaxis.describe(times)}")
    return Stream(ds=ds, times=times, lon=lon, lat=lat, ydim=ydim, xdim=xdim)


def check_same_grid(a: Stream, b: Stream, what: str) -> None:
    if a.lon.shape != b.lon.shape or not (
        np.allclose(a.lon, b.lon, atol=1e-4) and np.allclose(a.lat, b.lat, atol=1e-4)
    ):
        raise ValueError(f"{what}: input streams are on different grids; regrid before tracking")


def check_same_times(a: Stream, b: Stream, what: str) -> None:
    if len(a.times) != len(b.times) or any(x != y for x, y in zip(a.times, b.times)):
        raise ValueError(
            f"{what}: input streams have different time axes after alignment to DT="
            f"{cfg.DT:g} h ({timeaxis.describe(a.times)} vs {timeaxis.describe(b.times)})"
        )
