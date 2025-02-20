# import cartopy.crs as ccrs
# import geopandas as gpd
# import matplotlib.pyplot as plt
# import numpy as np
# import regionmask
# import xarray as xr
# from cartopy import feature as cfeature

# # Input specifications
# input_file = "./era5_daily_PR_202410.nc"
# output_file = "./tracking_region.nc"
# shapefile = '/Users/daniel/Downloads/demarcaciones-hidrograficas-phc-2022_27/DemarcHidrograficas_mayo2023.shp'

# # Load the NetCDF file and extract dimensions
# file = xr.open_dataset(input_file)
# longitude = np.array(file.longitude)
# latitude = np.array(file.latitude)

# # Read the shapefile
# ds = gpd.read_file(shapefile)

# # Reproject to WGS84 if necessary
# if ds.crs is None or ds.crs.to_epsg() != 4326:
#     ds = ds.to_crs(epsg=4326)

# # Add a unique ID for each region if not already present
# if 'region_id' not in ds.columns:
#     ds['region_id'] = range(len(ds))

# # Create a regionmask.Regions object
# regions = regionmask.Regions(
#     outlines=ds.geometry,
#     numbers=ds['region_id'],
#     names=ds['region_id'].astype(str)  # Use the IDs as names
# )
# # Generate the mask with unique numbers for each region
# mask = regions.mask(lon_or_obj=longitude, lat=latitude)

# # Save the mask as a NetCDF file
# export = xr.Dataset({"region_mask": (["latitude", "longitude"], mask)})
# export["longitude"] = ("longitude", longitude)
# export["latitude"] = ("latitude", latitude)
# export.to_netcdf(output_file)

# # Visualize the regions
# fig = plt.figure(figsize=(16, 10), dpi=200)
# ax = fig.add_subplot(111, projection=ccrs.PlateCarree())

# # Add map features
# ax.add_feature(cfeature.LAND, zorder=1, edgecolor="k", facecolor="w")
# ax.add_feature(cfeature.COASTLINE, zorder=2, linewidth=0.8)
# ax.add_feature(cfeature.BORDERS, zorder=2, linewidth=0.2, alpha=0.5)
# ax.add_feature(cfeature.RIVERS, zorder=2, linewidth=3, alpha=0.5)

# # Plot the mask
# mask_plot = mask.values
# contour = ax.contourf(longitude, latitude, mask_plot, cmap="tab20c", zorder=3, alpha=0.8)

# # Add a color bar
# cbar = plt.colorbar(contour, ax=ax, orientation="horizontal", pad=0.05, aspect=50)
# cbar.set_label("Region IDs", fontsize=12)

# # Grid and extent
# gl = ax.gridlines(draw_labels=True, alpha=0.5, linestyle=":", color="k")
# gl.top_labels = False
# gl.right_labels = False
# ax.set_extent([longitude.min(), longitude.max(), latitude.min(), latitude.max()])

# # Save the plot
# plt.savefig("regions_map.png", dpi=200)
# plt.show()


import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import regionmask
import xarray as xr
from cartopy import feature as cfeature
from scipy.ndimage import binary_dilation


# Input specifications
input_file = "./era5_daily_PR_202410.nc"
input_lm_file = "./era5_sst_lsm_2021412.nc"
output_file = "./watershed_mask_medsea.nc"
watershed_shapefile = '/home/dargueso/Misc_data/Demarcaciones_hidrograficas_mayo_2023/DemarcHidrograficas_mayo2023.shp'
ocean_shapfile = '/home/dargueso/Misc_data/Ocean_regions_GOaS_v1_20211214/goas_v01.shp'

# Selected watersheds for Mediterranean region
watersheds = {'CAT': 22,
              'EBR': 23,
              'JUC': 16,
              'BAL': 8,
              'SEG': 7,
              'SUR': 21}

ocean_regions = {'MED': 6}

ws_values = list(watersheds.values())
oc_values = list(ocean_regions.values())

# Load the NetCDF file and extract dimensions
file = xr.open_dataset(input_file)
longitude = file.longitude.values
latitude = file.latitude.values

# Load the land-sea mask (from SST)
lm_file = xr.open_dataset(input_lm_file)
sst = lm_file.sst.values
# replace Nan values with -9999
sst[np.isnan(sst)] = -9999
# create a mask for land
land_mask = (sst == -9999).squeeze()


#####################################################################
#####################################################################
## READ SHAPEFILES

# Read the shapefile watersheds
ds = gpd.read_file(watershed_shapefile)

# Reproject to WGS84 if necessary
if ds.crs is None or ds.crs.to_epsg() != 4326:
    ds = ds.to_crs(epsg=4326)

# Add a unique ID for each region if not already present
if 'region_id' not in ds.columns:
    ds['region_id'] = range(len(ds))

# Create a regionmask.Regions object
regions = regionmask.Regions(
    outlines=ds.geometry,
    numbers=ds['region_id'],
    names=ds['region_id'].astype(str)  # Use the IDs as names
)

#####################################################################

# Read the shapefile ocean
ds_ocean = gpd.read_file(ocean_shapfile)

# Reproject to WGS84 if necessary
if ds_ocean.crs is None or ds_ocean.crs.to_epsg() != 4326:
    ds_ocean = ds_ocean.to_crs(epsg=4326)

if 'ocean_id' not in ds_ocean.columns:
    ds_ocean['ocean_id'] = range(len(ds_ocean))

# Create a regionmask.Regions object
oceans = regionmask.Regions(
    outlines=ds_ocean.geometry,
    numbers=ds_ocean['ocean_id'],
    names=ds_ocean['ocean_id'].astype(str)  # Use the IDs as names
)

#####################################################################
#####################################################################

# Create a longitude/latitude grid using xarray
lon, lat = np.meshgrid(longitude, latitude)
lon_da = xr.DataArray(lon, dims=["latitude", "longitude"], coords={"latitude": latitude, "longitude": longitude})
lat_da = xr.DataArray(lat, dims=["latitude", "longitude"], coords={"latitude": latitude, "longitude": longitude})

# Generate the mask
mask = regions.mask(lon_or_obj=lon, lat=lat)

# Generate the mask with unique numbers for each region
mask_ocean = oceans.mask(lon_or_obj=lon, lat=lat)

#####################################################################
#####################################################################
# Create a mask for the Spanish Mediterranean (buffer zone)

med_ws_mask = mask.isin(ws_values).astype(bool)

# Step 2: Define the dilation parameters.
# Here, we create a disk-shaped (circular) footprint with radius 10.
radius = 10
y, x = np.ogrid[-radius:radius+1, -radius:radius+1]
footprint = x**2 + y**2 <= radius**2

# Step 3: Dilate the original area.
# This operation “grows” the True values outward by 10 grid points.
dilated_mask = binary_dilation(med_ws_mask, structure=footprint)

# Step 4: Compute the buffer zone.
# The buffer zone is defined as those cells that are in the dilated area
# but were not part of the original area.
buffer_zone = dilated_mask & (~med_ws_mask) 


medsea_mask = (~land_mask) & buffer_zone & mask_ocean.isin(oc_values)
mask.data[medsea_mask] = np.nanmax(mask.data) + 1



# Save the mask as a NetCDF file
export = xr.Dataset({"region_mask": (["latitude", "longitude"], mask.data)})  # Extract data explicitly
export["longitude"] = ("longitude", longitude)
export["latitude"] = ("latitude", latitude)
export.to_netcdf(output_file)

#####################################################################
#####################################################################

## PLOT REGIONS

# Visualize the regions
fig = plt.figure(figsize=(16, 10), dpi=200)
ax = fig.add_subplot(111, projection=ccrs.PlateCarree())

# Add map features
ax.add_feature(cfeature.LAND, zorder=1, edgecolor="k", facecolor="w")
ax.add_feature(cfeature.COASTLINE, zorder=2, linewidth=0.8)
ax.add_feature(cfeature.BORDERS, zorder=2, linewidth=0.2, alpha=0.5)
ax.add_feature(cfeature.RIVERS, zorder=2, linewidth=3, alpha=0.5)

# Plot the mask
contour = ax.contourf(longitude, latitude, mask.data, cmap="tab20c", zorder=3, alpha=0.8)

# Add a color bar
cbar = plt.colorbar(contour, ax=ax, orientation="horizontal", pad=0.05, aspect=50)
cbar.set_label("Region IDs", fontsize=12)

# Grid and extent
gl = ax.gridlines(draw_labels=True, alpha=0.5, linestyle=":", color="k")
gl.top_labels = False
gl.right_labels = False
ax.set_extent([longitude.min(), longitude.max(), latitude.min(), latitude.max()])

# Save the plot
plt.savefig("regions_map.png", dpi=200)
plt.show()
