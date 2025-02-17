import numpy as np
import xarray as xr
from tqdm import tqdm
from glob import glob
import json
import pandas as pd
from colorama import Fore, Style

# Define watersheds
watersheds = {
    'CAT': 22,
    'EBR': 23,
    'JUC': 16,
    'BAL': 8,
    'SEG': 7,
    'SUR': 21
}

def load_watershed_mask(file_path):
    """Load the watershed mask. Mock if file not available."""
    try:
        return xr.open_dataset(file_path)
    except FileNotFoundError:
        print("Warning: 'watershed_mask.nc' file not found. Using mock data.")
        # Return mock dataset for testing
        return xr.Dataset({"mock_mask": (("x", "y"), np.random.randint(0, 2, size=(100, 100)))})


def initialize_data_structures(total_months, watersheds):
    """Initialize trend dictionaries and data structures."""
    trends = {ws: np.zeros(total_months) for ws in watersheds.keys()}
    maxmean_trends = {ws: np.zeros(total_months) for ws in watersheds.keys()}
    max_trends = {ws: np.zeros(total_months) for ws in watersheds.keys()}
    object_stats = {ws: {} for ws in watersheds.keys()}
    return trends, maxmean_trends,max_trends, object_stats

def process_files(filesin, watersheds, total_months):
    """Process input files to compute trends and statistics."""
    # Initialize data structures
    wpr_trends = {ws: np.zeros(total_months) for ws in watersheds.keys()}
    wpr_maxmean_trends = {ws: np.zeros(total_months) for ws in watersheds.keys()}
    wpr_max_trends = {ws: np.zeros(total_months) for ws in watersheds.keys()}
    object_stats = {ws: [] for ws in watersheds.keys()}
    col_trends = np.zeros(total_months)
    col_objects_id = []
    for year_idx, filein in enumerate(tqdm(filesin, desc="Processing files")):
        tqdm.write(Fore.GREEN + f'Processing file {filein} ({year_idx + 1}/{len(filesin)})')
        fin= xr.open_dataset(filein, chunks={'time': 365})  # Use chunking for large files

        for month in range(1, 13):
            month_idx = (month - 1) + (12 * year_idx)  # Monthly index
            monthly_data = fin.sel(time=fin.time.dt.month == month)

            # Extract COL objects
            col_objects = np.unique(monthly_data.col_objects.values)
            col_trends[month_idx] = len(col_objects[col_objects != 0])  # Count unique COL objects
            col_objects_id.append(np.unique(monthly_data.col_objects.values))

            # Process COL days
            col_mask = (monthly_data.col_objects != 0).compute()  # Compute the mask
            col_days = monthly_data.where(col_mask, drop=True)  # Apply the computed mask
            if not col_days.time.size:  # Skip if no COL days
                continue

            pr_col_days = monthly_data.pr.sel(time=col_days.time)
            pr_max_col_days = monthly_data.pr_max.sel(time=col_days.time)

            # Compute value for each watershed
            for watershed in watersheds.keys():
                wpr_trends[watershed][month_idx] = pr_col_days.where(ws_mask.region_mask == watersheds[watershed]).sum().values
                wpr_maxmean_trends[watershed][month_idx] = pr_max_col_days.where(ws_mask.region_mask == watersheds[watershed]).mean().values
                wpr_max_trends[watershed][month_idx] = pr_max_col_days.where(ws_mask.region_mask == watersheds[watershed]).max().values

            # Process each COL object individually for object-wise maxima
            for obj_id in col_objects[col_objects != 0]:
                obj_mask = (monthly_data.col_objects == obj_id).compute()
                obj_data = monthly_data.where(obj_mask, drop=True)

                for watershed in watersheds.keys():
                    ws_precip = obj_data.pr.where(ws_mask.region_mask == watersheds[watershed], drop=True)

                    if ws_precip.size > 0:
                        max_precip_rate = ws_precip.max().values
                        max_precip_accum = ws_precip.sum(dim='time').max().values
                        object_stats[watershed].append({
                            'object_id': obj_id,
                            'year': monthly_data.time.dt.year.values[0],
                            'month': month,
                            'max_pr_rate': max_precip_rate,
                            'max_pr_accum': max_precip_accum,
                            'ws_pr_accum': ws_precip.sum().values
                        })


    # Extract maximum rainfall across grid points
    return wpr_trends, wpr_maxmean_trends,col_objects_id,object_stats

def save_all_data(
    wpr_trends, wpr_maxmean_trends,col_objects_id,object_stats
):
    """Save all processed data to JSON and CSV."""
    all_data = {
        "wpr_trends": {k: v.tolist() for k, v in wpr_trends.items()},
        "wpr_maxmean_trends": {k: v.tolist() for k, v in wpr_maxmean_trends.items()},
        "col_objects_id": col_objects_id,
        "object_stats": object_stats,
    }

    # Save all data as JSON
    with open("all_data.json", "w") as json_file:
        json.dump(all_data, json_file, indent=4)
    print("All data saved to all_data.json")


# Main execution
if __name__ == "__main__":
    # Load watershed mask
    ws_mask = load_watershed_mask('watershed_mask.nc')

    # Gather input files
    files_in = sorted(glob('./era5_daily_col_z500_????.nc'))
    files_in = files_in[:5]
    total_months = 12 * len(files_in)  # 12 months per year

    # Process files and compute trends and rainfall accumulation
    (
        wpr_trends,
        wpr_maxmean_trends,
        col_objects_id,
        object_stats,
    ) = process_files(files_in, watersheds, total_months)

    import pdb; pdb.set_trace()  # fmt: skip
    # Save all results
    save_all_data(
        wpr_trends,
        wpr_maxmean_trends,
        col_objects_id,
        object_stats,
    )
