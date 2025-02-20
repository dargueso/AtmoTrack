import xarray as xr
import time as time
import numpy as np
import pandas as pd
from tqdm import tqdm
import os
import glob

import atmotrack_config as cfg

from dask.distributed import Client
from dask.diagnostics import ProgressBar
from colorama import Fore, Style, init
init(autoreset=True)

def deaccumulate_era5land(ds):
    """ Deaccumulate the ERA5land precipitation data
        Hourly values are accumulated since the beginning of the day
        So they are reset at 01:00 UTC, and they refer to the accumulated since 00:00 UTC
    """
    print(f"{Style.BRIGHT} Deaccumulating ERA5land data")
    time_at_01 = ds.valid_time.dt.hour == 1
    diff = ds.tp.diff(dim='valid_time')
    diff = xr.concat([ds.tp.isel(valid_time=0), diff], dim='valid_time')
    diff = diff.where(~time_at_01, ds['tp'])
    
    return diff


def main ():

    # Start a local Dask cluster
    client = Client(n_workers=8, threads_per_worker=2, memory_limit='16GB')
    
    years = np.arange(1940,2025)
    col_min_dur = cfg.col_min_dur
    DT = cfg.DT

    #Define patters of files

    lores = './data_tracking/era5_daily_col_z500_????.nc'
    hires = '/home/dargueso/ERA5/ERA5land/era5land_daily_PR_*.nc'

    # Load atmo data

    lores_files_all = sorted(glob.glob(lores))
    hires_files_all = sorted(glob.glob(hires))

    # Load the watershed mask once (assumed to be at 0.25° resolution)

    ws_mask_lores = xr.open_dataset('watershed_mask_medsea.nc')

    #Create hires mask via interpolation

    hires_file_ref = xr.open_dataset(hires_files_all[0])
    ws_mask_hires = ws_mask_lores.interp(
        latitude=hires_file_ref.latitude, 
        longitude=hires_file_ref.longitude, 
        method='nearest')
    
    # Create dictionary with watershed codes and names
    watersheds = {'CAT': 22, 'EBR': 23, 'JUC': 16, 'BAL': 8,
                  'SEG': 7, 'SUR': 21, 'MED': 25}


    for year in years:

        print(f"{Fore.GREEN} Processing year {year}")

        all_results = []

        # Load the data for the year
        lores_file = [f for f in lores_files_all if str(year) in f][0] # lores files are annual
        hires_files = [f for f in hires_files_all if f.split('_')[-1][:4] == str(year)]

        ds_lores = xr.open_dataset(lores_file, chunks={'time': -1, 'latitude': 261, 'longitude': 201})

        if len(hires_files) > 0:

            # Open dataset with proper chunking
            ds_hires_acc = xr.open_mfdataset(hires_files, combine='by_coords',
                                            chunks={'valid_time': -1, 'latitude': 651, 'longitude': 501})

            # Compute the difference along valid_time
            ds_hires_diff = ds_hires_acc.diff(dim='valid_time')

            # Extract the first time step and ensure its dimensions match ds_hires_diff
            first_step = ds_hires_acc.isel(valid_time=0).expand_dims(dim="valid_time", axis=0)

            # Concatenate along valid_time to restore the original size
            ds_hires = xr.concat([first_step, ds_hires_diff], dim='valid_time')
            ds_hires['tp'] = ds_hires.tp.where(ds_hires.tp>0,0)
            ds_hires = ds_hires.where(ds_hires.valid_time.dt.hour != 1, ds_hires_acc.tp)
            ds_hires = ds_hires.where(~ds_hires_acc.tp.isnull(), np.nan)
            ds_hires *= 1000.0  # Convert from m to mm

            pr_max_hires = ds_hires.resample(valid_time='6h').max()
            pr_sum_hires = ds_hires.resample(valid_time='6h').sum()
                    

        else:
            print(f"{Fore.YELLOW} No hires data found for year {year}")
            ds_hires = None

        # Get objects from the lores file
        # Bring col_objects into memory; pr_max remains lazy

        col_objs = ds_lores.col_objects.compute()
        pr_max_lores = ds_lores.pr_max #This is the hourly maximum over the previous 6 hours
        pr_sum_lores = ds_lores.pr #This is the accumulated precipitation over the previous 6 hours

        objects_ids = np.unique(col_objs.values)
        objects_ids = objects_ids[objects_ids != 0] # 0 is the background
        objects_ids = objects_ids.astype(int)

        # Loop over all objects to extract the stats for each of them

        for obj in tqdm(objects_ids, desc=f"Processing objects for {year}"):
            time_mask = (col_objs == obj).any(dim=['latitude', 'longitude'])
            time_true = np.where(time_mask.values)[0]
            if time_true[-1] == time_mask.size - 1:
                extended_time_indices = np.arange(time_true[0], time_true[-1] + 1)
            else:
                extended_time_indices = np.arange(time_true[0], time_true[-1] + 2)
            month = ds_lores.time.isel(time=extended_time_indices[0]).dt.month.item()

            if extended_time_indices.size < col_min_dur/DT:
                print(f"{Fore.YELLOW} Object {obj} does not meet the minimum duration of {col_min_dur} hours")
                continue

            # Extract precipitation during event (including gaps when the object is not present)
            pr_event_max_lores = pr_max_lores.isel(time=extended_time_indices).max(dim='time').compute()
            pr_event_sum_lores = pr_sum_lores.isel(time=extended_time_indices).sum(dim='time').compute()

            if len(hires_files) > 0:
                pr_event_max_hires = pr_max_hires.isel(valid_time=extended_time_indices).max(dim='valid_time').compute()
                pr_event_sum_hires = pr_sum_hires.isel(valid_time=extended_time_indices).sum(dim='valid_time').compute()

            # ---- # Extract the maximum precipitation during the event (no matter where)
            pr_event_max_max_lores = pr_event_max_lores.max().compute()
            pr_event_sum_max_lores = pr_event_sum_lores.max().compute()
            flat_index_max = pr_event_max_lores.argmax().compute().item()
            lat_idx, lon_idx = np.unravel_index(flat_index_max, pr_event_max_lores.shape)
            lat_val = pr_event_max_lores.latitude.values[lat_idx]
            lon_val = pr_event_max_lores.longitude.values[lon_idx]
            ws_value = ws_mask_lores['region_mask'].sel(latitude=lat_val, longitude=lon_val, method='nearest').item()
            watershed_name = next((k for k, v in watersheds.items() if v == ws_value), None)


            # ---- Extract stats for each watershed --- #

            ws_event_max_lores = {}
            ws_event_sum_max_lores = {}
            ws_event_sum_lores = {}
            ws_event_mean_lores = {}
            ws_event_max_hires = {}
            ws_event_sum_max_hires = {}
            ws_event_sum_hires = {}
            ws_event_mean_hires = {}

            for ws_name, ws_id in watersheds.items():

                
                pr_event_max_ws_lores = pr_event_max_lores.where(ws_mask_lores['region_mask'] == ws_id,drop=True)
                pr_event_sum_ws_lores = pr_event_sum_lores.where(ws_mask_lores['region_mask'] == ws_id,drop=True)

                ws_event_max_lores[ws_name] = pr_event_max_ws_lores.max().compute().item()
                ws_event_sum_max_lores[ws_name] = pr_event_sum_ws_lores.max().compute().item()
                ws_event_sum_lores[ws_name] = pr_event_sum_ws_lores.sum().compute().item()
                ws_event_mean_lores[ws_name] = pr_event_sum_ws_lores.mean().compute().item()

                if len(hires_files) > 0:

                    pr_event_max_ws_hires = pr_event_max_hires.where(ws_mask_hires['region_mask'] == ws_id,drop=True)
                    pr_event_sum_ws_hires = pr_event_sum_hires.where(ws_mask_hires['region_mask'] == ws_id,drop=True)

                    ws_event_max_hires[ws_name] = pr_event_max_ws_hires.tp.max().compute().item()
                    ws_event_sum_max_hires[ws_name] = pr_event_sum_ws_hires.tp.max().compute().item()
                    ws_event_sum_hires[ws_name] = pr_event_sum_ws_hires.tp.sum().compute().item()
                    ws_event_mean_hires[ws_name] = pr_event_sum_ws_hires.tp.mean().compute().item()
           
                else:
                    ws_event_max_hires[ws_name] = np.nan
                    ws_event_sum_max_hires[ws_name] = np.nan
                    ws_event_sum_hires[ws_name] = np.nan
                    ws_event_mean_hires[ws_name] = np.nan
            
            # Save the stats for the object
            new_obj_id = f"{obj}_{year}"

            all_results.append({
                'object_id': new_obj_id,
                'year': year,
                'month': month,
                'duration': extended_time_indices.size * DT,
                'max_precip_lores': pr_event_max_max_lores.item(),
                'latitude': lat_val,
                'longitude': lon_val,
                'watershed': watershed_name,
                'max_precip_ws_lores': ws_event_max_lores,
                'max_precip_ws_hires': ws_event_max_hires,
                'max_sum_precip_ws_lores': ws_event_sum_max_lores,
                'max_sum_precip_ws_hires': ws_event_sum_max_hires,
                'mean_precip_ws_lores': ws_event_mean_lores,
                'mean_precip_ws_hires': ws_event_mean_hires,
                'sum_precip_ws_lores': ws_event_sum_lores,
                'sum_precip_ws_hires': ws_event_sum_hires,
            })
    
        #Close files
        ds_lores.close()
        if ds_hires is not None:
            ds_hires_acc.close()
        
        df_events = pd.DataFrame(all_results)
        df_events.to_csv(f'./events_stats/events_stats_{year}.csv', index=False)
        print(f"{Style.BRIGHT} {len(all_results)} events processed")
        print(f"{Style.BRIGHT} Saved events stats to events_stats_{year}.csv")
        print(f"{Fore.GREEN} Done year {year}!")


if __name__ == '__main__':
    main()
