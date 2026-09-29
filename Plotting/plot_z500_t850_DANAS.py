#!/usr/bin/env python
"""
@File    :  plot_z500_t850_DANAS.py
@Time    :  2025/02/21 14:25:03
@Author  :  Daniel Argüeso
@Version :  1.0
@Contact :  d.argueso@uib.es
@License :  (C)Copyright 2022, Daniel Argüeso
@Project :  None
@Desc    :  None
"""

import argparse
import os
import warnings

import cartopy.crs as ccrs
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import xarray as xr
from matplotlib.colors import BoundaryNorm

warnings.filterwarnings("ignore", category=RuntimeWarning, module="shapely")

# Set up the argument parser to get the date from the command line
parser = argparse.ArgumentParser(description="Plot Z500 and T850 with wind barbs.")
parser.add_argument("date", type=str, help="Date in YYYY-MM-DD format, e.g. 1987-11-03")
parser.add_argument("--input", default=None, help="Input netCDF file (default: extracted_DATE.nc)")
parser.add_argument(
    "--output-dir", default=None, dest="output_dir", help="Output directory (default: DATE)"
)
args = parser.parse_args()

filein = args.input if args.input is not None else f"extracted_{args.date}.nc"
folder = args.output_dir if args.output_dir is not None else args.date
if not os.path.exists(folder):
    os.makedirs(folder)


lat = xr.open_dataset(filein).latitude.squeeze().values
lon = xr.open_dataset(filein).longitude.squeeze().values

data = xr.open_dataset(filein)

lon2d, lat2d = np.meshgrid(lon, lat)

lon_pcolormesh = lon - np.mean(np.diff(lon) * 0.5)
lat_pcolormesh = lat - np.mean(np.diff(lat) * 0.5)

clon = lon[lon.size // 2]
clat = lat[lat.size // 2]

# for tstep in len(data.time):


mylevels = np.arange(500, 600, 5)
cmap = sns.color_palette("Spectral_r", as_cmap=True)
norm = BoundaryNorm(mylevels, ncolors=cmap.N, extend="both")


###########################################################

for idate in range(len(data.time)):
    z500 = data.z500.isel(time=idate)
    t850 = data.t850.isel(time=idate)
    u850 = data.u850.isel(time=idate)
    v850 = data.v850.isel(time=idate)
    obj = data.col_objects.isel(time=idate)
    date = pd.to_datetime(str(data.isel(time=idate).time.values))

    obj_mask = (obj > 0).astype(int)

    # PLOTTING
    cproj = ccrs.PlateCarree(central_longitude=clon, globe=None)

    fig = plt.figure(figsize=(12, 12))
    ax = fig.add_subplot(1, 1, 1, projection=cproj)

    # ax.add_feature(cfeature.OCEAN, zorder=100,facecolor=[24/255,  116/255,  205/255])
    # ax.add_feature(cfeature.LAND, zorder=100, edgecolor=None,facecolor=[24/255,  116/255,  205/255])
    # ax.add_feature(cfeature.LAKES, zorder=100,linewidth=0.5,edgecolor='k',facecolor=[24/255,  116/255,  205/255])

    # Plotting t850
    line_contour = ax.contour(
        lon2d,
        lat2d,
        t850,
        levels=np.arange(250, 300, 5),
        colors="b",
        linewidths=2,
        transform=ccrs.PlateCarree(),
        zorder=103,
    )
    ax.clabel(
        line_contour,  # Typically best results when labelling line contours.
        colors=["b"],
        manual=False,  # Automatic placement vs manual placement.
        inline=True,  # Cut the line where the label will be placed.
        fmt=" {:.0f} ".format,  # Labes as integers, with some extra space.
        zorder=103,
    )

    # Use the line contours to place contour labels.
    # contourf = ax.pcolormesh(lon2d, lat2d, z500/100., cmap = cmap,norm=norm, transform=ccrs.PlateCarree(),zorder=101)
    contourf = ax.contourf(
        lon2d,
        lat2d,
        z500 / 100.0,
        cmap=cmap,
        levels=mylevels,
        extend="both",
        transform=ccrs.PlateCarree(),
        zorder=101,
    )

    # Plottting wind
    skip = 10
    ax.barbs(
        lon2d[::skip, ::skip],
        lat2d[::skip, ::skip],
        u850.values[::skip, ::skip],
        v850.values[::skip, ::skip],
        length=5,  # Smaller length for smaller barb
        transform=ccrs.PlateCarree(),
        zorder=104,
    )

    # Plotting objects
    c = ax.contourf(
        lon2d,
        lat2d,
        obj_mask,
        levels=[0.5, 1.5],
        colors="none",
        hatches=["///"],
        transform=ccrs.PlateCarree(),
        zorder=105,
    )
    # Draw the hatching in red (ContourSet.collections was removed in matplotlib 3.10).
    c.set_edgecolor("red")

    ax.coastlines(linewidth=0.5, zorder=102, resolution="50m")
    gl = ax.gridlines(
        crs=ccrs.PlateCarree(),
        xlocs=range(-180, 181, 10),
        ylocs=range(-80, 81, 10),
        draw_labels=True,
        zorder=102,
        linewidth=0.2,
        color="k",
        alpha=1,
        linestyle="--",
    )
    gl.top_labels = False
    gl.right_labels = False

    cbar = plt.colorbar(contourf, shrink=0.65)
    cbar.set_label("Z500 (dam)", fontsize=12)

    ax.set_title(f"Z500 and T850 {date.strftime('%Y-%m-%d %H:%M')} UTC", fontsize=14)
    plt.savefig(f"{folder}/z500_t850_{date.strftime('%Y%m%d%H')}.png")
