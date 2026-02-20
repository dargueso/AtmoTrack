#!/usr/bin/env python
"""
plot_shapefile_multiple_regions.py — Visualise all regions in a shapefile.

Each region is drawn in a different random colour.

Usage:
  python plot_shapefile_multiple_regions.py --shapefile /path/to/regions.shp
  python plot_shapefile_multiple_regions.py --shapefile /path/to/regions.shp --output regions_map.png
"""

import argparse
import random

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.pyplot as plt


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--shapefile",
        required=True,
        help="Shapefile (.shp) to visualise",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Save plot to this file instead of showing it interactively",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    gdf = gpd.read_file(args.shapefile)
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notnull()]
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
    colors = [f"#{random.randint(0, 0xFFFFFF):06x}" for _ in range(len(gdf))]

    fig, ax = plt.subplots(
        figsize=(10, 10),
        subplot_kw={"projection": ccrs.PlateCarree()},
    )
    for (_, row), color in zip(gdf.iterrows(), colors):
        ax.add_geometries(
            [row.geometry],
            crs=ccrs.PlateCarree(),
            facecolor=color,
            edgecolor="black",
        )
    ax.set_extent(
        [bounds[0], bounds[2], bounds[1], bounds[3]],
        crs=ccrs.PlateCarree(),
    )
    ax.set_title("Shapefile Region Map with Different Colors", fontsize=15)

    if args.output:
        plt.savefig(args.output, dpi=200)
        print(f"Plot saved to {args.output}")
    else:
        plt.show()


if __name__ == "__main__":
    main()
