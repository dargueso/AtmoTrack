#!/usr/bin/env python
'''
@File    :  plot_z500_t850_DANAS.py
@Time    :  2025/02/21 14:25:03
@Author  :  Daniel Argüeso
@Version :  1.0
@Contact :  d.argueso@uib.es
@License :  (C)Copyright 2022, Daniel Argüeso
@Project :  None
@Desc    :  None
'''



import cartopy.crs as ccrs
import matplotlib.pyplot as plt
import xarray as xr
import netCDF4 as nc
import numpy as np
import cartopy.util as cutil
import cartopy.feature as cfeature
from glob import glob
import pandas as pd
import matplotlib.colors as mcolors
from matplotlib.colors import BoundaryNorm
import matplotlib.patches as patches
import seaborn as sns
import warnings
import os
import re
import argparse
warnings.filterwarnings("ignore", category=RuntimeWarning, module="shapely")

# Set up the argument parser to get the date from the command line

# Construct the input file name based on the provided date
filein = f'./data_tracking/era5_daily_col_z500_2024.nc'
lat = xr.open_dataset(filein).latitude.squeeze().values
lon = xr.open_dataset(filein).longitude.squeeze().values

data = xr.open_dataset(filein)

lon2d, lat2d = np.meshgrid(lon, lat)

lon_pcolormesh=lon-np.mean(np.diff(lon)*0.5)
lat_pcolormesh=lat-np.mean(np.diff(lat)*0.5)

clon = lon[lon.size//2]
clat = lat[lat.size//2]


#PLOTTING
cproj = ccrs.PlateCarree(central_longitude=clon, globe=None)

fig = plt.figure(figsize=(12, 12))
ax = fig.add_subplot(1,1,1,projection=cproj)

# ax.add_feature(cfeature.OCEAN, zorder=100,facecolor=[24/255,  116/255,  205/255])
# ax.add_feature(cfeature.LAND, zorder=100, edgecolor=None,facecolor=[24/255,  116/255,  205/255])
# ax.add_feature(cfeature.LAKES, zorder=100,linewidth=0.5,edgecolor='k',facecolor=[24/255,  116/255,  205/255])
lon_min, lon_max = lon2d.min(), lon2d.max()
ax.plot([lon_min, lon_max], [88, 88], color='gold', linewidth=2, label='88°N',transform=ccrs.PlateCarree())
ax.plot([lon_min, lon_max], [20, 20], color='lightskyblue', linewidth=2, label='20°N',transform=ccrs.PlateCarree())
ax.plot([lon_min, lon_max], [70, 70], color='lightskyblue', linewidth=2, label='20°N',transform=ccrs.PlateCarree())

# Add a green square in 15°W - 10°E, 30°N - 45°N
rect1 = patches.Rectangle((-15, 30), 25, 15, linewidth=2, edgecolor='green', facecolor='none',transform=ccrs.PlateCarree())
ax.add_patch(rect1)

# Add a dotted blue square in 29°W - 19°E, 25°N - 60°N
rect2 = patches.Rectangle((-29, 25), 48, 35, linewidth=2, edgecolor='blue', facecolor='none',transform=ccrs.PlateCarree())
ax.add_patch(rect2)

ax.coastlines(linewidth=0.5,zorder=102,resolution="50m")
gl = ax.gridlines(crs=ccrs.PlateCarree(), xlocs=range(-180,181,10), ylocs=range(-80,81,10),
                draw_labels=True, zorder=102,
                linewidth=0.2, color='k', alpha=1, linestyle='--')
gl.top_labels = False
gl.right_labels = False

# Add a thick black border along lat2d and lon2d
lat_min, lat_max = lat2d.min(), lat2d.max()
lon_min, lon_max = lon2d.min(), lon2d.max()

# Define the border coordinates
border_lons = [lon_min, lon_max, lon_max, lon_min, lon_min]
border_lats = [lat_min, lat_min, lat_max, lat_max, lat_min]

# Plot the border
ax.plot(border_lons, border_lats, 'k-', linewidth=3, label='Region Border',transform=ccrs.PlateCarree())


plt.tight_layout()
plt.savefig(f"region_DANAS.png")
