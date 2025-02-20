import geopandas as gpd
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.io.shapereader as shpreader

# Load the shapefile
shapefile_path = '/home/dargueso/Misc_data/Demarcacion_hidrografica_Jucar/F162C175_Demarcacion.shp'
gdf = gpd.read_file(shapefile_path)

# Filter out invalid geometries
gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notnull()]

# Reproject to WGS84 if necessary
if gdf.crs is None or gdf.crs.to_epsg() != 4326:
    gdf = gdf.to_crs(epsg=4326)

# Get bounds and adjust for Cartopy
manual_bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]

# Create a map with Cartopy
fig, ax = plt.subplots(
    figsize=(10, 10),
    subplot_kw={'projection': ccrs.PlateCarree()}
)

# Add features from GeoDataFrame to Cartopy
for _, row in gdf.iterrows():
    ax.add_geometries(
        [row.geometry],
        crs=ccrs.PlateCarree(),
        facecolor='orange',
        edgecolor='black'
    )

# Set the extent of the map
ax.set_extent([manual_bounds[0], manual_bounds[2], manual_bounds[1], manual_bounds[3]], crs=ccrs.PlateCarree())

# Add title and show the map
ax.set_title('Shapefile Region Map', fontsize=15)
plt.show()



# gdf.plot(figsize=(10, 10), color='orange', edgecolor='black')
# plt.title("Shapefile Data Validation")
# plt.show()