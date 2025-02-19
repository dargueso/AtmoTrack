from dask.distributed import Client
import xarray as xr
import time
from dask.diagnostics import ProgressBar
import numpy as np
import pandas as pd
from tqdm import tqdm
import glob
import os
import atmotrack_config as cfg

def main():
    # Start a local Dask cluster
    client = Client(n_workers=8, threads_per_worker=2, memory_limit='16GB')
    
    # Define file patterns for low-res and high-res files
    lowres_pattern = './data_tracking/era5_daily_col_z500_????.nc'
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
        # e.g., "era5_daily_col_z500_1970.nc" -> "1970"
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
            ds_6h_max = None
            ds_6h_sum = None
            ws_mask_hires = None
        else:
            # Open all matching high-res files for the year as one dataset
            ds_pr = xr.open_mfdataset(matching_highres, combine='by_coords',
                                      chunks={'valid_time': -1, 'latitude': 651, 'longitude': 501})
            #Deacumulate every hour at 24h steps


            time_at_00 = ds_pr.valid_time.dt.hour == 0
            tp = ds_pr['tp']
            hourly_precip = tp.copy()
            hourly_precip[1:,:,:] = tp.diff(dim='valid_time', n=1)
            hourly_precip = hourly_precip.where(~time_at_00, tp)


            # Resample high-res precipitation to 6-hourly:
            # ds_6h_max: instantaneous (max) rainfall per 6H period
            ds_6h_max = hourly_precip.resample(valid_time='6h').max()
            # ds_6h_sum: accumulated rainfall (sum) over each 6H period
            ds_6h_sum = hourly_precip.resample(valid_time='6h').sum()
            # Interpolate the watershed mask to the hi-res grid (using nearest neighbor)
            ws_mask_hires = ws_mask.interp(latitude=ds_6h_max.latitude, longitude=ds_6h_max.longitude, method='nearest')
        

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
        pr_sum = ds['pr']

        
        # Get unique object IDs for this year (ignoring 0 and NaN)
        object_ids = np.unique(col_objs.values)
        object_ids = object_ids[~np.isnan(object_ids)]
        object_ids = object_ids[object_ids != 0]
        
        for obj in tqdm(object_ids, desc=f"Processing objects for {year}"):
            # Process low-res data for each object
            # mask_obj = (col_objs == obj)
            time_mask = (col_objs == obj).any(dim=['latitude', 'longitude'])
            time_true = np.where(time_mask.values)[0]
   
            # Create an extended time index that spans from the first to the last occurrence.
            extended_time_indices = np.arange(time_true[0], time_true[-1] + 1)
            
            if extended_time_indices.size < cfg.col_min_dur/cfg.DT:
                continue
            # Extract precipitation data for the entire domain for all time steps between first and last occurrence.
            pr_period_max = pr_max.isel(time=extended_time_indices).compute()
            pr_period_sum = pr_sum.isel(time=extended_time_indices).compute()
            if pr_period_max.size == 0 or pr_period_sum.size == 0:
                continue

            # Find the global maximum and its location
            # (time, latitude, longitude) of the global maximum
            # (and its value) in the object's data
            # Also, find the watershed of the global maximum
            max_val = pr_period_max.max().item()
            flat_index = int(np.nanargmax(pr_period_max.values))
            time_idx, lat_idx, lon_idx = np.unravel_index(flat_index, pr_period_max.shape)
            time_val = pr_period_max.time.values[time_idx]
            lat_val = pr_period_max.latitude.values[lat_idx]
            lon_val = pr_period_max.longitude.values[lon_idx]
            ws_value = ws_mask['region_mask'].sel(latitude=lat_val, longitude=lon_val, method='nearest').item()
            watershed_name = next((k for k, v in watersheds.items() if v == ws_value), None)

    
            # For each watershed, compute maximum from original low-res data
            ws_max_lores = {}
            ws_sum_lores = {}
            for ws_name, ws_id in watersheds.items():
                pr_period_max_ws = pr_period_max.where(ws_mask['region_mask'] == ws_id, drop=True)
                pr_period_sum_ws = pr_period_sum.where(ws_mask['region_mask'] == ws_id, drop=True)

                if pr_period_max_ws.size == 0 or pr_period_sum_ws.size == 0:
                    ws_max_lores[ws_name] = None
                    ws_sum_lores[ws_name] = None

                else:
                    try:
                        ws_max_val = pr_period_max_ws.max().item()
                        ws_sum_val = pr_period_sum_ws.mean(dim=['latitude','longitude']).sum().item()
                    except Exception:
                        ws_max_val = None
                        ws_sum_val = None
                    ws_max_lores[ws_name] = ws_max_val
                    ws_sum_lores[ws_name] = ws_sum_val


            # Process hi-res data if available; otherwise, set hi-res values to None.
            if ds_6h_max is None or ds_6h_sum is None:
                ws_max_hires = {ws_name: None for ws_name in watersheds.keys()}
                ws_sum_hires = {ws_name: None for ws_name in watersheds.keys()}
            else:
                                
                if len(extended_time_indices) == 0:
                    pr_obj_hires_max = None
                    pr_obj_hires_sum = None
                else:
                    # Restrict the hi-res instantaneous (max) precipitation to the object's time period
                    pr_obj_hires_max = ds_6h_max.isel(valid_time=extended_time_indices).compute()
                    # Restrict the hi-res accumulated (sum) precipitation to the object's time period
                    pr_obj_hires_sum = ds_6h_sum.isel(valid_time=extended_time_indices).compute()
    
                # For hi-res instantaneous (max) data, compute per-watershed maximum (converted to mm)
                ws_max_hires = {}
                if pr_obj_hires_max is None or pr_obj_hires_max.size == 0:
                    for ws_name in watersheds.keys():
                        ws_max_hires[ws_name] = None
                else:
                    for ws_name, ws_id in watersheds.items():
                        pr_period_max_ws_hires = pr_obj_hires_max.where(ws_mask_hires['region_mask'] == ws_id, drop=True)
                        if pr_period_max_ws_hires.size == 0:
                            ws_max_hires[ws_name] = None
                        else:
                            try:
                                ws_max_val_hires = pr_period_max_ws_hires.max().item()
                            except Exception:
                                ws_max_val_hires = None
                            ws_max_hires[ws_name] = ws_max_val_hires * 1000 if ws_max_val_hires is not None else None
    
                # For hi-res accumulated (sum) data, compute total (accumulated) precipitation per watershed (converted to mm/grid point)
                ws_sum_hires = {}
                if pr_obj_hires_sum is None or pr_obj_hires_sum.size == 0:
                    for ws_name in watersheds.keys():
                        ws_sum_hires[ws_name] = None
                else:
                    for ws_name, ws_id in watersheds.items():
                        pr_period_max_ws_hires_sum = pr_obj_hires_sum.where(ws_mask_hires['region_mask'] == ws_id, drop=True)
                        if pr_period_max_ws_hires_sum.size == 0:
                            ws_sum_hires[ws_name] = None
                        else:
                            try:
                                ws_sum_val_hires = pr_period_max_ws_hires_sum.mean(dim=['latitude','longitude']).sum().item()
                            except Exception:
                                ws_sum_val_hires = None
                            ws_sum_hires[ws_name] = ws_sum_val_hires * 1000 if ws_sum_val_hires is not None else None
    
            # Extract event month for reference (if needed)
            month = pd.to_datetime(str(time_val)).strftime('%m')
            # Redefine object id as "objectid_year"
            new_obj_id = f"{obj}_{year}"
            all_results.append({
                'object_id': new_obj_id,
                'year': year,
                'month': month,
                'max_pr': max_val,           # global maximum from low-res
                'time': time_val,
                'latitude': lat_val,
                'longitude': lon_val,
                'watershed': watershed_name,
                'ws_max_lores': ws_max_lores,            # per-watershed maximum from original low-res data
                'ws_sum_lores': ws_sum_lores,            # per-watershed total from original low-res dataws_sum
                'ws_max_hires': ws_max_hires,            # per-watershed instantaneous hi-res (max) precipitation
                'ws_sum_hires': ws_sum_hires             # per-watershed total (accumulated) hi-res precipitation (wpr_trends)
            })
    
        ds.close()
        if ds_pr is not None:
            ds_pr.close()
    
    # Combine event-level results from all years into one DataFrame and save to CSV
    df_events = pd.DataFrame(all_results)
    df_events.to_csv('object_results_events.csv', index=False)
    print("\nEvent-level results saved to 'object_results_events.csv'")
    print(df_events.head())
    

if __name__ == '__main__':
    main()
