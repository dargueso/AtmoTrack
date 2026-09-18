#!/usr/bin/env python
"""
download_ERA5_PW.py — Download ERA5 monthly-mean precipitable water.

Precipitable water (PW) is ERA5's "total column water vapour" (tcwv, kg m-2,
numerically equal to mm). Data are requested from the CDS monthly-means
product (reanalysis-era5-single-levels-monthly-means), one request per year,
and written as annual NetCDF files:

  era5_monthly_TCWV_YYYY.nc  — monthly-mean total column water vapour

Downloads are resumable: years whose file already exists are skipped.

Prerequisites:
  - A Copernicus CDS account and a valid ~/.cdsapirc credentials file.
    See: https://cds.climate.copernicus.eu/how-to-api
  - pip install cdsapi xarray netCDF4

Usage examples:
  # Download the whole ERA5 period up to the last complete month
  python download_ERA5_PW.py --year-start 1940 --current-year

  # Download a range of years
  python download_ERA5_PW.py --year-start 1940 --year-end 2024

  # Explicit years, custom area [N W S E] and output directory
  python download_ERA5_PW.py --years 2023 2024 --area 45 -10 35 5 --outdir /scratch/era5
"""

import argparse
import logging
import pathlib
from datetime import date

import cdsapi

import atmotrack_config as cfg
from download_ERA5 import DEFAULT_AREA

API = "reanalysis-era5-single-levels-monthly-means"
VARIABLE = "total_column_water_vapour"
PREFIX = "era5_monthly_TCWV"
FIRST_YEAR = 1940


def download_year(client, year, months, outdir, area, overwrite=False):
    """Submit one CDS request for the given months of a year. Returns the output Path.

    An existing file is kept (and the request skipped) unless ``overwrite`` is set.
    """
    target = outdir / f"{PREFIX}_{year:04d}.nc"

    if target.exists() and not overwrite:
        logging.info(f"  {target.name} already exists — skipping")
        return target

    request = {
        "product_type": ["monthly_averaged_reanalysis"],
        "data_format": "netcdf",
        "download_format": "unarchived",
        "variable": [VARIABLE],
        "year": [f"{year}"],
        "month": [f"{m:02d}" for m in months],
        "time": ["00:00"],
        "area": area,
    }

    logging.info(f"  Requesting {target.name} (months {months[0]:02d}–{months[-1]:02d}) …")
    # Download to a temporary name so an interrupted transfer is not mistaken
    # for a finished file on the next run.
    tmp = target.with_suffix(".nc.part")
    client.retrieve(API, request, str(tmp))
    tmp.replace(target)
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    year_group = parser.add_mutually_exclusive_group(required=True)
    year_group.add_argument(
        "--year-start",
        type=int,
        help="First year to download (use with --year-end or --current-year)",
    )
    year_group.add_argument(
        "--years",
        type=int,
        nargs="+",
        metavar="YEAR",
        help="Explicit list of years to download",
    )
    parser.add_argument(
        "--year-end",
        type=int,
        help="Last year to download, inclusive",
    )
    parser.add_argument(
        "--current-year",
        action="store_true",
        help=(
            "Extend the range to the current year, including its months that are "
            "already complete (the current-year file is re-downloaded on every run)"
        ),
    )
    parser.add_argument(
        "--area",
        type=float,
        nargs=4,
        default=DEFAULT_AREA,
        metavar=("N", "W", "S", "E"),
        help="Bounding box [N W S E] in degrees (default: %(default)s)",
    )
    parser.add_argument(
        "--outdir",
        type=pathlib.Path,
        default=pathlib.Path(cfg.data_era5),
        help="Output directory (default: data_era5 from config.toml)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    logging.basicConfig(
        format="%(asctime)s | %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO,
    )

    today = date.today()
    if args.years:
        years = sorted(args.years)
    else:
        if args.current_year:
            year_end = today.year
        elif args.year_end is not None:
            year_end = args.year_end
        else:
            raise SystemExit("--year-start requires --year-end or --current-year")
        years = list(range(args.year_start, year_end + 1))

    if years[0] < FIRST_YEAR:
        raise SystemExit(f"ERA5 starts in {FIRST_YEAR}; got {years[0]}")

    args.outdir.mkdir(parents=True, exist_ok=True)
    client = cdsapi.Client()

    for year in years:
        logging.info(f"=== Year {year} ===")
        if year < today.year:
            months = list(range(1, 13))
        elif year == today.year:
            # Monthly means are only published once a month is complete.
            months = list(range(1, today.month))
            if not months:
                logging.info("  No complete month yet — skipping")
                continue
        else:
            logging.warning(f"  {year} is in the future — skipping")
            continue

        try:
            # The current-year file grows as months are added: always refresh it.
            download_year(
                client, year, months, args.outdir, args.area, overwrite=(year == today.year)
            )
        except Exception as exc:
            # The last month or two may not be on the CDS yet; do not abort the run.
            logging.error(f"  Request for {year} failed: {exc}")

    logging.info("Download complete.")


if __name__ == "__main__":
    main()
