#!/usr/bin/env python
"""
select_region_ERA5_shapefile.py — Create a binary region mask from a shapefile.

Reads an ERA5 NetCDF to obtain the lat/lon grid, applies a shapefile region
mask via regionmask, and writes a NetCDF containing a ``source_region``
variable (1 inside the region, 0 outside).

Usage:
  python select_region_ERA5_shapefile.py \\
      --input  ./era5_daily_PR_202410.nc \\
      --shapefile /path/to/region.shp \\
      --output ./mask_region.nc

  # Select specific sub-regions by 0-based index:
  python select_region_ERA5_shapefile.py \\
      --input  ./era5_daily_PR_202410.nc \\
      --shapefile /path/to/region.shp \\
      --output ./mask_region.nc \\
      --regions 0 1 2
"""

import argparse

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import regionmask
import xarray as xr
from cartopy import feature as cfeature


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        required=True,
        help="ERA5 NetCDF file used to read the lat/lon grid",
    )
    parser.add_argument(
        "--shapefile",
        required=True,
        help="Shapefile (.shp) defining the region boundary",
    )
    parser.add_argument(
        "--output",
        default="./mask_region.nc",
        help="Output NetCDF path (default: ./mask_region.nc)",
    )
    parser.add_argument(
        "--regions",
        type=int,
        nargs="+",
        default=None,
        metavar="IDX",
        help=("0-based indices of sub-regions to include (default: first region only)"),
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Retrieve file dimensions
    file = xr.open_dataset(args.input)
    longitude = np.array(file.longitude)
    latitude = np.array(file.latitude)

    # Read shapefile
    ds = gpd.read_file(args.shapefile)
    if ds.crs is None or ds.crs.to_epsg() != 4326:
        ds = ds.to_crs(epsg=4326)

    # Generate mask
    mask = regionmask.mask_3D_geopandas(ds, longitude, latitude)

    if args.regions is not None:
        region_ids = args.regions
        new_masks = mask.isel(region=region_ids).values
    else:
        new_masks = mask.isel(region=[0]).values

    # Convert boolean to 0/1 and collapse multiple regions to 2-D
    new_masks = np.where(not new_masks, 0, 1)
    new_masks = np.nanmean(new_masks, axis=0)

    # Export as NetCDF
    export = xr.Dataset({"source_region": (["latitude", "longitude"], new_masks.astype(float))})
    export["longitude"] = ("longitude", longitude)
    export["latitude"] = ("latitude", latitude)
    export.to_netcdf(args.output)
    print(f"Mask written to {args.output}")

    # Visualise the selected region
    fontsizes = 10

    fig = plt.figure(figsize=(16, 10), dpi=200)
    ax = fig.add_subplot(111, projection=ccrs.PlateCarree())

    ax.add_feature(cfeature.LAND, zorder=1, edgecolor="k", facecolor="w")
    ax.add_feature(cfeature.COASTLINE, zorder=2, linewidth=0.8)
    ax.add_feature(cfeature.BORDERS, zorder=2, linewidth=0.2, alpha=0.5)
    ax.add_feature(cfeature.RIVERS, zorder=2, linewidth=3, alpha=0.5)
    ax.add_feature(cfeature.STATES, zorder=2, facecolor="w")
    ax.add_feature(
        cfeature.LAKES,
        zorder=2,
        linewidth=0.8,
        edgecolor="k",
        alpha=0.5,
        facecolor="w",
    )

    indices = export.where(export.source_region == 1, drop=True)
    lon_indices = indices.longitude
    lon_indices_c = [float(i) * -1 if float(i) <= 180 else float(i) for i in lon_indices]

    ax.contourf(longitude, latitude, new_masks, levels=[0.1, 1], zorder=5, alpha=0.8)
    ax.set_extent(
        [
            min(lon_indices_c) - 3,
            max(lon_indices_c) + 3,
            float(indices.latitude.min()) - 3,
            float(indices.latitude.max()) + 3,
        ]
    )

    gl = ax.gridlines(draw_labels=True, alpha=0.5, linestyle=":", color="k")
    gl.top_labels = False
    gl.right_labels = False
    gl.xlabel_style = {"size": fontsizes * 2}
    gl.ylabel_style = {"size": fontsizes * 2}

    plt.savefig("source_region.png", dpi=200)
    print("Plot saved to source_region.png")


if __name__ == "__main__":
    main()
