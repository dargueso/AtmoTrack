import cartopy.crs as ccrs  # used for plotting on a map
import geopandas as gpd  # reading shapefiles
import matplotlib.patheffects as pe  # additional plotting functionalities
import matplotlib.pyplot as plt  # used for plotting purposes
import numpy as np  # used for array calculations
import regionmask  # used for region mapping
import xarray as xr  # used to read .nc files
from cartopy import feature as cfeature
#Specify Input
input_file = "./era5_daily_PR_202410.nc"
output_file = "./mask_region.nc"
shapefile = "/home/dargueso/Misc_data/Demarcacion_hidrografica_Jucar/F162C175_Demarcacion.shp"
regions = False


# Retrieve file dimensions
file = xr.open_dataset(input_file)

longitude = np.array(file.longitude)
latitude = np.array(file.latitude)

# Read shapefile
ds = gpd.read_file(shapefile)  # load shape file
# Reproject to WGS84 if necessary
if ds.crs is None or ds.crs.to_epsg() != 4326:
    ds = ds.to_crs(epsg=4326)
# Generate mask
mask = regionmask.mask_3D_geopandas(ds, longitude, latitude)

# Specify region ID - what to store
if regions != False:
    region_ids = np.array(regions)
    region_ids = region_ids.tolist()

    if type(region_ids) == int:
        raise KeyError(
            "regions not specified correctly, please check whether your input is provided as a list [...,...], even if it is a single source region!"
        )
    import pdb; pdb.set_trace()  # fmt: skip
    new_masks = mask.isel(region=region_ids).values

    # Make WAM2layers fitting
    new_masks = np.where(
        new_masks == False, 0, 1
    )  # Replace True and False by 1 and 0's
    new_masks = np.nanmean(new_masks, axis=0)  # Multiple masks into 1D array
else:
    new_masks = mask.isel(region=[0]).values

    # Make WAM2layers fitting
    new_masks = np.where(
        new_masks == False, 0, 1
    )  # Replace True and False by 1 and 0's
    new_masks = np.nanmean(new_masks, axis=0)  # Multiple masks into 1D array

# Export as source region for WAM2layers

# create xarray dataset
data = new_masks

export = xr.Dataset(
    {"source_region": (["latitude", "longitude"], data.astype(float))}
)

# set coordinates
export["longitude"] = ("longitude", longitude)
export["latitude"] = ("latitude", latitude)

# save to NetCDF file

export.to_netcdf(output_file)



# Visualise all regions available

fontsizes = 10
pads = 20

fig = plt.figure(figsize=(16, 10),dpi=200)
ax = fig.add_subplot(111, projection=ccrs.PlateCarree())

# Make figure
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
mask_plot = new_masks

indices = export.where(export.source_region==1,drop=True)
lon_indices = indices.longitude
lon_indices_c = []
for i in lon_indices:
    if i <= 180:
        item = i * -1
        lon_indices_c.append(item)
    else:
        lon_indices_c.append(i)

lat_indices = indices.latitude
        
ax.contourf(longitude, latitude, mask_plot, levels=[0.1, 1],zorder=5,alpha=0.8)
ax.set_extent([min(lon_indices_c)-3,max(lon_indices_c)+3,indices.latitude.min()-3,indices.latitude.max()+3])
    
# Grid
gl = ax.gridlines(draw_labels=True, alpha=0.5, linestyle=":", color="k")
gl.top_labels = False
gl.right_labels = False
gl.xlabel_style = {"size": fontsizes*2}
gl.ylabel_style = {"size": fontsizes*2}

plt.savefig("source_region.png",dpi=200)
 