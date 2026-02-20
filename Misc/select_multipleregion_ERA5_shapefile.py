#!/usr/bin/env python
"""
select_multipleregion_ERA5_shapefile.py — Build the watershed + ocean mask for AtmoTrack.

Combines a watershed shapefile and an ocean shapefile to produce the
``region_mask`` NetCDF used by ``calc_COL_stats_all_watersheds.py``.

Steps:
  1. Read watershed and ocean shapefiles and rasterise them onto the ERA5 grid.
  2. Dilate the selected watershed polygons by a configurable radius.
  3. Intersect the buffer zone with the ocean mask to define the Mediterranean
     sea region and assign it the next available region ID.
  4. Write the combined mask to a NetCDF file.

Usage:
  python select_multipleregion_ERA5_shapefile.py \\
      --input             ./era5_daily_PR_202410.nc \\
      --lsm               ./era5_sst_lsm.nc \\
      --watershed-shp     /path/to/watersheds.shp \\
      --ocean-shp         /path/to/ocean_regions.shp \\
      --output            ./watershed_mask_medsea.nc
"""

import argparse

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import regionmask
import xarray as xr
from cartopy import feature as cfeature
from scipy.ndimage import binary_dilation

import atmotrack_config as cfg

# Selected watersheds for the Mediterranean region (region IDs in the shapefile)
WATERSHEDS = {
    "CAT": 22,
    "EBR": 23,
    "JUC": 16,
    "BAL": 8,
    "SEG": 7,
    "SUR": 21,
}

# Ocean region to include (region ID in the ocean shapefile)
OCEAN_REGIONS = {"MED": 6}

# Dilation radius in grid points
DILATION_RADIUS = 10


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
        "--lsm",
        required=True,
        help="ERA5 SST/land-sea mask NetCDF (variable: sst)",
    )
    parser.add_argument(
        "--watershed-shp",
        required=True,
        help="Watershed shapefile (.shp)",
    )
    parser.add_argument(
        "--ocean-shp",
        required=True,
        help="Ocean regions shapefile (.shp)",
    )
    parser.add_argument(
        "--output",
        default=cfg.watershed_mask,
        help=f"Output NetCDF path (default: {cfg.watershed_mask})",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Load ERA5 grid dimensions
    file = xr.open_dataset(args.input)
    longitude = file.longitude.values
    latitude = file.latitude.values

    # Load land-sea mask from SST
    lm_file = xr.open_dataset(args.lsm)
    sst = lm_file.sst.values
    sst[np.isnan(sst)] = -9999
    land_mask = (sst == -9999).squeeze()

    # ------------------------------------------------------------------ #
    # Read watershed shapefile
    # ------------------------------------------------------------------ #
    ds = gpd.read_file(args.watershed_shp)
    if ds.crs is None or ds.crs.to_epsg() != 4326:
        ds = ds.to_crs(epsg=4326)
    if "region_id" not in ds.columns:
        ds["region_id"] = range(len(ds))

    regions = regionmask.Regions(
        outlines=ds.geometry,
        numbers=ds["region_id"],
        names=ds["region_id"].astype(str),
    )

    # ------------------------------------------------------------------ #
    # Read ocean shapefile
    # ------------------------------------------------------------------ #
    ds_ocean = gpd.read_file(args.ocean_shp)
    if ds_ocean.crs is None or ds_ocean.crs.to_epsg() != 4326:
        ds_ocean = ds_ocean.to_crs(epsg=4326)
    if "ocean_id" not in ds_ocean.columns:
        ds_ocean["ocean_id"] = range(len(ds_ocean))

    oceans = regionmask.Regions(
        outlines=ds_ocean.geometry,
        numbers=ds_ocean["ocean_id"],
        names=ds_ocean["ocean_id"].astype(str),
    )

    # ------------------------------------------------------------------ #
    # Rasterise onto ERA5 grid
    # ------------------------------------------------------------------ #
    lon, lat = np.meshgrid(longitude, latitude)
    mask = regions.mask(lon_or_obj=lon, lat=lat)
    mask_ocean = oceans.mask(lon_or_obj=lon, lat=lat)

    # ------------------------------------------------------------------ #
    # Build Mediterranean sea region via dilation + ocean intersection
    # ------------------------------------------------------------------ #
    ws_values = list(WATERSHEDS.values())
    oc_values = list(OCEAN_REGIONS.values())

    med_ws_mask = mask.isin(ws_values).astype(bool)

    y, x = np.ogrid[-DILATION_RADIUS : DILATION_RADIUS + 1, -DILATION_RADIUS : DILATION_RADIUS + 1]
    footprint = x**2 + y**2 <= DILATION_RADIUS**2
    dilated_mask = binary_dilation(med_ws_mask, structure=footprint)
    buffer_zone = dilated_mask & (~med_ws_mask)

    medsea_mask = (~land_mask) & buffer_zone & mask_ocean.isin(oc_values)
    mask.data[medsea_mask] = np.nanmax(mask.data) + 1

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    export = xr.Dataset({"region_mask": (["latitude", "longitude"], mask.data)})
    export["longitude"] = ("longitude", longitude)
    export["latitude"] = ("latitude", latitude)
    export.to_netcdf(args.output)
    print(f"Watershed mask written to {args.output}")

    # ------------------------------------------------------------------ #
    # Plot
    # ------------------------------------------------------------------ #
    fig = plt.figure(figsize=(16, 10), dpi=200)
    ax = fig.add_subplot(111, projection=ccrs.PlateCarree())

    ax.add_feature(cfeature.LAND, zorder=1, edgecolor="k", facecolor="w")
    ax.add_feature(cfeature.COASTLINE, zorder=2, linewidth=0.8)
    ax.add_feature(cfeature.BORDERS, zorder=2, linewidth=0.2, alpha=0.5)
    ax.add_feature(cfeature.RIVERS, zorder=2, linewidth=3, alpha=0.5)

    contour = ax.contourf(longitude, latitude, mask.data, cmap="tab20c", zorder=3, alpha=0.8)
    cbar = plt.colorbar(contour, ax=ax, orientation="horizontal", pad=0.05, aspect=50)
    cbar.set_label("Region IDs", fontsize=12)

    gl = ax.gridlines(draw_labels=True, alpha=0.5, linestyle=":", color="k")
    gl.top_labels = False
    gl.right_labels = False
    ax.set_extent([longitude.min(), longitude.max(), latitude.min(), latitude.max()])

    plt.savefig("regions_map.png", dpi=200)
    print("Plot saved to regions_map.png")


if __name__ == "__main__":
    main()
