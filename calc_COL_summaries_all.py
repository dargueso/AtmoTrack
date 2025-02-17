import pickle
import numpy as np
import xarray as xr
from tqdm import tqdm
from colorama import Fore, Style
from glob import glob

# Watersheds definition
watersheds = {'CAT': 22,
              'EBR': 23,
              'JUC': 16,
              'BAL': 8,
              'SEG': 7,
              'SUR': 21,
              'MED': 25}

# Load the watershed mask
ws_mask = xr.open_dataset('watershed_mask_medsea.nc')

# Gather input files
filesin = sorted(glob('./era5_daily_col_z500_????.nc'))

# Gather PR ERA5 land data

#filesin_era5land = sorted(glob('/home/dargueso/ERA5/ERA5land/era5land_daily_PR_??????.nc'))

# Total number of years and months
total_months = 12 * len(filesin)  # 12 months per year

# Initialize rolling dictionaries (1D arrays with total_months size)
wpr_trends = {watershed: np.zeros(total_months) for watershed in watersheds.keys()}
wpr_max_trends = {watershed: np.zeros(total_months) for watershed in watersheds.keys()}
wpr_maxmean_trends = {watershed: np.zeros(total_months) for watershed in watersheds.keys()}
col_trends = np.zeros(total_months)
col_objects_id = []

# Dictionary to store object-wise maximum precipitation for each watershed
obj_stats = {watershed: [] for watershed in watersheds.keys()}

# Processing files
col_list = []
for year_idx, filein in enumerate(tqdm(filesin, desc="Processing files")):
    tqdm.write(Fore.GREEN + f'Processing file {filein} ({year_idx + 1}/{len(filesin)})')
    fin = xr.open_dataset(filein, chunks={'time': 365})  # Use chunking for large files
    for month in range(1, 13):
        month_idx = month - 1 + 12 * year_idx  # Calculate 1D index for this month
        monthly_data = fin.sel(time=fin.time.dt.month == month)

        # Extract COL objects
        col_objects = np.unique(monthly_data.col_objects.values)
        col_trends[month_idx] = len(col_objects[col_objects != 0])  # Count unique COL objects
        col_objects_id.append(np.unique(monthly_data.col_objects.values))
        
        # Process COL days
        col_mask = (monthly_data.col_objects != 0).compute()  # Compute the mask
        col_days = monthly_data.where(col_mask, drop=True)    # Apply the computed mask

        if not col_days.time.size:  # Skip if no COL days
            continue

        pr_col_days = monthly_data.pr.sel(time=col_days.time)
        pr_max_col_days = monthly_data.pr_max.sel(time=col_days.time)

        # Compute trends for each watershed
        for watershed in watersheds.keys():
            import pdb; pdb.set_trace()  # fmt: skip
            wpr_trends[watershed][month_idx] = pr_col_days.where(ws_mask.region_mask == watersheds[watershed]).sum().values
            wpr_maxmean_trends[watershed][month_idx] = pr_max_col_days.where(ws_mask.region_mask == watersheds[watershed]).mean().values
            wpr_max_trends[watershed][month_idx] = pr_max_col_days.where(ws_mask.region_mask == watersheds[watershed]).max().values

        # Process each COL object individually for object-wise maxima
        for obj_id in col_objects[col_objects != 0]:
            obj_mask = (monthly_data.col_objects == obj_id).compute()  # Compute the mask
            obj_data = monthly_data.where(obj_mask, drop=True)  
            # Compute maximum precipitation for each watershed
            for watershed in watersheds.keys():
                ws_precip = obj_data.pr.where(ws_mask.region_mask == watersheds[watershed], drop=True)

                if ws_precip.size > 0:  # Only record if there's data
                    max_pr_rate = ws_precip.max().values
                    max_pr_accum = ws_precip.sum(dim='time').max().values
                    obj_stats[watershed].append({
                        'object_id': obj_id,
                        'year': monthly_data.time.dt.year.values[0],
                        'month': month,
                        'max_pr_rate': max_pr_rate,
                        'max_pr_accum': max_pr_accum,
                        'ws_pr_accum': ws_precip.sum().values,
                    })


# Save results to a single file
output_file = "combined_results_all_years.pkl"

with open(output_file, 'wb') as f:
    pickle.dump({
        'monthly_trends': {
            'wpr_trends': wpr_trends,
            'wpr_max_trends': wpr_max_trends,
            'wpr_maxmean_trends': wpr_maxmean_trends,
            'col_trends': col_trends,
            'col_objects_id': col_objects_id
        },
        'object_stats': obj_stats
    }, f)

tqdm.write(Style.BRIGHT + f"Saved all results to {output_file}")
