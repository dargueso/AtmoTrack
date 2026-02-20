#!/usr/bin/env python
"""
plot_shapefile.py — Visualise a single-region shapefile on a map.

Usage:
  python plot_shapefile.py --shapefile /path/to/region.shp
  python plot_shapefile.py --shapefile /path/to/region.shp --output region_map.png
"""

import argparse

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

    fig, ax = plt.subplots(
        figsize=(10, 10),
        subplot_kw={"projection": ccrs.PlateCarree()},
    )
    for _, row in gdf.iterrows():
        ax.add_geometries(
            [row.geometry],
            crs=ccrs.PlateCarree(),
            facecolor="orange",
            edgecolor="black",
        )
    ax.set_extent(
        [bounds[0], bounds[2], bounds[1], bounds[3]],
        crs=ccrs.PlateCarree(),
    )
    ax.set_title("Shapefile Region Map", fontsize=15)

    if args.output:
        plt.savefig(args.output, dpi=200)
        print(f"Plot saved to {args.output}")
    else:
        plt.show()


if __name__ == "__main__":
    main()
