#!/usr/bin/env python
"""
``atmotrack-download-era5`` — Download ERA5 data required by AtmoTrack.

Requests ERA5 variables month-by-month from the Copernicus CDS API and
concatenates them into annual NetCDF files that match AtmoTrack's naming
convention:

  era5_daily_500hPa_YYYY.nc  — 500 hPa geopotential + temperature   (6-hourly)
  era5_daily_200hPa_YYYY.nc  — 200 hPa u-wind                        (6-hourly)
  era5_daily_300hPa_YYYY.nc  — 300 hPa u-wind                        (6-hourly)
  era5_daily_850hPa_YYYY.nc  — 850 hPa u,v-wind + temperature        (6-hourly)
  era5_daily_SLP_YYYY.nc     — Mean sea-level pressure                (3-hourly)
  era5_daily_PR_YYYY.nc      — Total precipitation                    (hourly)

Prerequisites:
  - A Copernicus CDS account and a valid ~/.cdsapirc credentials file.
    See: https://cds.climate.copernicus.eu/how-to-api
  - pip install cdsapi xarray netCDF4

Usage examples:
  # Download all datasets for 1979–2024
  atmotrack-download-era5 --year-start 1979 --year-end 2024

  # Download only Z500 and SLP for two specific years
  atmotrack-download-era5 --years 2020 2023 --datasets z500 slp

  # Download the current (possibly incomplete) year up to last available month
  atmotrack-download-era5 --current-year

  # Keep the intermediate per-month files in addition to the annual files
  atmotrack-download-era5 --year-start 2000 --year-end 2024 --keep-monthly

  # Write to a custom directory instead of the one in config.toml
  atmotrack-download-era5 --year-start 2020 --year-end 2024 --outdir /scratch/era5
"""

import argparse
import logging
import pathlib
from datetime import date

import cdsapi
import xarray as xr

from atmotrack import config as cfg

# ---------------------------------------------------------------------------
# Time-step lists
# ---------------------------------------------------------------------------
_DAYS = [f"{d:02d}" for d in range(1, 32)]
_TIMES_6H = ["00:00", "06:00", "12:00", "18:00"]
_TIMES_3H = ["00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00"]
_TIMES_1H = [f"{h:02d}:00" for h in range(24)]

# Default geographic domain [North, West, South, East]
DEFAULT_AREA = [75, -30, 10, 20]

# ---------------------------------------------------------------------------
# Dataset catalogue
# Each entry describes one CDS request type and its output file prefix.
# ---------------------------------------------------------------------------
DATASETS = {
    "z500": dict(
        api="reanalysis-era5-pressure-levels",
        variables=["geopotential", "temperature"],
        levels=["500"],
        times=_TIMES_6H,
        prefix="era5_daily_500hPa",
        description="500 hPa geopotential + temperature",
    ),
    "z200": dict(
        api="reanalysis-era5-pressure-levels",
        variables=["u_component_of_wind"],
        levels=["200"],
        times=_TIMES_6H,
        prefix="era5_daily_200hPa",
        description="200 hPa u-wind",
    ),
    "z300": dict(
        api="reanalysis-era5-pressure-levels",
        variables=["u_component_of_wind"],
        levels=["300"],
        times=_TIMES_6H,
        prefix="era5_daily_300hPa",
        description="300 hPa u-wind",
    ),
    "z850": dict(
        api="reanalysis-era5-pressure-levels",
        variables=["u_component_of_wind", "v_component_of_wind", "temperature"],
        levels=["850"],
        times=_TIMES_6H,
        prefix="era5_daily_850hPa",
        description="850 hPa u,v-wind + temperature",
    ),
    "slp": dict(
        api="reanalysis-era5-single-levels",
        variables=["mean_sea_level_pressure"],
        levels=None,
        times=_TIMES_3H,
        prefix="era5_daily_SLP",
        description="Mean sea-level pressure",
    ),
    "pr": dict(
        api="reanalysis-era5-single-levels",
        variables=["total_precipitation"],
        levels=None,
        times=_TIMES_1H,
        prefix="era5_daily_PR",
        description="Total precipitation",
    ),
}


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def download_month(client, ds_key, year, month, monthly_dir, area):
    """Submit one CDS request for a single month. Returns the output Path.

    If the file already exists it is returned immediately (safe to resume an
    interrupted download run).
    """
    ds = DATASETS[ds_key]
    target = monthly_dir / f"{ds['prefix']}_{year:04d}{month:02d}.nc"

    if target.exists():
        logging.info(f"    {target.name} already exists — skipping CDS request")
        return target

    request = {
        "product_type": "reanalysis",
        "data_format": "netcdf",
        "download_format": "unarchived",
        "variable": ds["variables"],
        "year": f"{year}",
        "month": f"{month:02d}",
        "day": _DAYS,
        "time": ds["times"],
        "area": area,
    }
    if ds["levels"] is not None:
        request["pressure_level"] = ds["levels"]

    logging.info(f"    Requesting {target.name} …")
    client.retrieve(ds["api"], request, str(target))
    return target


def concatenate_annual(monthly_files, annual_path):
    """Concatenate per-month NetCDFs into one annual file."""
    logging.info(f"    Concatenating {len(monthly_files)} months → {annual_path.name}")
    with xr.open_mfdataset(sorted(str(f) for f in monthly_files), combine="by_coords") as ds:
        ds.to_netcdf(str(annual_path))


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
        help="First year to download (use together with --year-end)",
    )
    year_group.add_argument(
        "--years",
        type=int,
        nargs="+",
        metavar="YEAR",
        help="Explicit list of years to download",
    )
    year_group.add_argument(
        "--current-year",
        action="store_true",
        help="Download the current (possibly incomplete) year up to the latest available month",
    )
    parser.add_argument(
        "--year-end",
        type=int,
        help="Last year to download, inclusive (required with --year-start)",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=list(DATASETS),
        default=list(DATASETS),
        metavar="DATASET",
        help=("Datasets to download. Choices: " + ", ".join(DATASETS) + " (default: all)"),
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
        help="Output directory for annual files (default: data_era5 from config.toml)",
    )
    parser.add_argument(
        "--keep-monthly",
        action="store_true",
        help="Keep intermediate per-month files after annual concatenation",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    logging.basicConfig(
        format="%(asctime)s | %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO,
    )

    # Resolve year list
    today = date.today()
    if args.current_year:
        years = [today.year]
    elif args.years:
        years = sorted(args.years)
    else:
        if args.year_end is None:
            raise SystemExit("--year-start requires --year-end")
        years = list(range(args.year_start, args.year_end + 1))

    # Output directories
    args.outdir.mkdir(parents=True, exist_ok=True)
    monthly_dir = args.outdir / "_monthly"
    monthly_dir.mkdir(exist_ok=True)

    client = cdsapi.Client()

    for year in years:
        logging.info(f"=== Year {year} ===")

        # For the current year only download months that have already started;
        # CDS returns whatever days are available within the requested month.
        last_month = today.month if year == today.year else 12

        for ds_key in args.datasets:
            ds = DATASETS[ds_key]
            logging.info(f"  [{ds_key}] {ds['description']}")

            annual_path = args.outdir / f"{ds['prefix']}_{year:04d}.nc"
            if annual_path.exists():
                logging.info(f"  {annual_path.name} already exists — skipping")
                continue

            # Download each month
            monthly_files = [
                download_month(client, ds_key, year, month, monthly_dir, args.area)
                for month in range(1, last_month + 1)
            ]

            # Concatenate into annual file
            concatenate_annual(monthly_files, annual_path)

            # Remove monthly intermediates unless --keep-monthly was requested
            if not args.keep_monthly:
                for f in monthly_files:
                    f.unlink(missing_ok=True)
                logging.info("    Monthly intermediates removed")

    # Remove the monthly staging directory if it is now empty
    if not args.keep_monthly:
        try:
            monthly_dir.rmdir()
        except OSError:
            pass  # not empty (e.g. partial runs from other datasets)

    logging.info("Download complete.")


if __name__ == "__main__":
    main()
