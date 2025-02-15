from dask.distributed import Client
import xarray as xr
import time
from dask.diagnostics import ProgressBar
import numpy as np
import pandas as pd
from tqdm import tqdm
import glob
import os

def main():
    # Start a local Dask cluster
    client = Client(n_workers=8, threads_per_worker=2, memory_limit='16GB')
    
    # Define file patterns for low-res and high-res files
    lowres_pattern = './era5_daily_col_z500_????.nc'
    highres_pattern = '/home/dargueso/ERA5/ERA5land/era5land_daily_PR_*.nc'
    
    # Get sorted list of low-res files (one per year)
    lowres_files = sorted(glob.glob(lowres_pattern))
    # Get sorted list of high-res files (monthly files)
    highres_files = sorted(glob.glob(highres_pattern))
    
    # Load the watershed mask once (assumed to be at 0.25° resolution)
    ws_mask = xr.open_dataset('watershed_mask_medsea.nc')
    
    # Define watershed mapping
    watersheds = {'CAT': 22, 'EBR': 23, 'JUC': 16, 'BAL': 8,
                  'SEG': 7, 'SUR': 21, 'MED': 25}
    
    all_results = []
    
    # Process each low-res file (one per year) separately
    for lowres_file in lowres_files:
        # Extract the year from the low-res filename.
        # Assuming filenames like: era5_daily_col_z500_1970.nc
        year = lowres_file.split('_')[-1].split('.')[0]
        print(f"\nProcessing year: {year}")
        
        # Open low-res dataset (with objects and pr_max) for the year
        ds = xr.open_dataset(lowres_file, chunks={'time': -1, 'latitude': 261, 'longitude': 201})
        print("Opened low-res dataset for year", year)
        
        # Find matching high-res files (monthly files) for the current year.
        matching_highres = []
        for f in highres_files:
            basename = os.path.basename(f)  # e.g., "era5land_daily_PR_197001.nc"
            year_month = basename.split('_')[-1].split('.')[0]  # e.g., "197001"
            file_year = year_month[:4]  # e.g., "1970"
            if file_year == year:
                matching_highres.append(f)
        print("Matching high-res files for year", year, ":", matching_highres)
        
        if len(matching_highres) == 0:
            print(f"No high-res files found for year {year}. Hi-res values will be set to None.")
            ds_pr = None
            ds_6h = None
            ws_mask_hires = None
        else:
            # Open all matching high-res files for the year as one dataset
            ds_pr = xr.open_mfdataset(matching_highres, combine='by_coords',
                                      chunks={'time': -1, 'latitude': 261, 'longitude': 201})
            # Resample high-res precipitation to 6-hourly (taking maximum over each 6H period)
            ds_6h = ds_pr.resample(valid_time='6H').max()
            # Interpolate the watershed mask to the high-res grid (using nearest neighbor)
            ws_mask_hires = ws_mask.interp(latitude=ds_6h.latitude, longitude=ds_6h.longitude, method='nearest')
        
        # Check that required variables exist in the low-res dataset
        if 'col_objects' not in ds or 'pr_max' not in ds:
            print(f"Low-res dataset for year {year} must contain 'col_objects' and 'pr_max' variables. Skipping.")
            ds.close()
            if ds_pr is not None:
                ds_pr.close()
            continue
        
        # Bring col_objects into memory; pr_max remains lazy
        col_objs = ds['col_objects'].compute()
        pr_max = ds['pr_max']
        
        # Get unique object IDs for this year (ignoring 0 and NaN)
        object_ids = np.unique(col_objs.values)
        object_ids = object_ids[~np.isnan(object_ids)]
        object_ids = object_ids[object_ids != 0]
        
        for obj in tqdm(object_ids, desc=f"Processing objects for {year}"):
            # Process low-res data for each object
            mask_obj = (col_objs == obj)
            pr_obj = pr_max.where(mask_obj, drop=True).compute()
            if pr_obj.size == 0:
                continue
            max_val = pr_obj.max().item()
            flat_index = int(np.nanargmax(pr_obj.values))
            time_idx, lat_idx, lon_idx = np.unravel_index(flat_index, pr_obj.shape)
            time_val = pr_obj.time.values[time_idx]
            lat_val = pr_obj.latitude.values[lat_idx]
            lon_val = pr_obj.longitude.values[lon_idx]
            ws_value = ws_mask['region_mask'].sel(latitude=lat_val, longitude=lon_val, method='nearest').item()
            watershed_name = next((k for k, v in watersheds.items() if v == ws_value), None)
            # For each watershed, compute maximum from original low-res data
            ws_max = {}
            for ws_name, ws_id in watersheds.items():
                pr_obj_ws = pr_obj.where(ws_mask['region_mask'] == ws_id, drop=True)
                if pr_obj_ws.size == 0:
                    ws_max[ws_name] = None
                else:
                    try:
                        ws_max_val = pr_obj_ws.max().item()
                    except Exception:
                        ws_max_val = None
                    ws_max[ws_name] = ws_max_val
    
            # Process hi-res data only if available; otherwise, set hi-res values to None.
            if ds_6h is None:
                ws_max_hires = {ws_name: None for ws_name in watersheds.keys()}
            else:
                # Determine time period when the object exists (from low-res col_objects)
                time_mask = (col_objs == obj).any(dim=['latitude', 'longitude'])
                time_indices = np.where(time_mask.values)[0]
                if len(time_indices) == 0:
                    pr_obj_hires = None
                else:
                    # Restrict the high-res 6H precipitation to the object's time period
                    pr_obj_hires = ds_6h.tp.isel(valid_time=time_indices).compute()
    
                # For each watershed, compute maximum from hi-res data over the object's period
                ws_max_hires = {}
                if pr_obj_hires is None or pr_obj_hires.size == 0:
                    for ws_name in watersheds.keys():
                        ws_max_hires[ws_name] = None
                else:
                    for ws_name, ws_id in watersheds.items():
                        pr_obj_ws_hires = pr_obj_hires.where(ws_mask_hires['region_mask'] == ws_id, drop=True)
                        if pr_obj_ws_hires.size == 0:
                            ws_max_hires[ws_name] = None
                        else:
                            try:
                                ws_max_val_hires = pr_obj_ws_hires.max().item()
                            except Exception:
                                ws_max_val_hires = None
                            ws_max_hires[ws_name] = ws_max_val_hires*1000  # Convert to mm
    
            # Redefine object id as "objectid_year"
            new_obj_id = f"{obj}_{year}"
            all_results.append({
                'object_id': new_obj_id,
                'year': year,
                'max_pr': max_val,
                'time': time_val,
                'latitude': lat_val,
                'longitude': lon_val,
                'watershed': watershed_name,
                'ws_max': ws_max,             # per-watershed maximum from original low-res data
                'ws_max_hires': ws_max_hires  # per-watershed maximum from hi-res data (or None)
            })
    
        ds.close()
        if ds_pr is not None:
            ds_pr.close()
    
    # Combine results from all years into one DataFrame and save to CSV
    df = pd.DataFrame(all_results)
    csv_file = 'object_results.csv'
    df.to_csv(csv_file, index=False)
    print("\nResults DataFrame saved to '{}'".format(csv_file))
    print(df)

if __name__ == '__main__':
    main()
