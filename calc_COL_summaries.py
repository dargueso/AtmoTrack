import pickle
import numpy as np
import xarray as xr
from tqdm import tqdm
from colorama import Fore, Style
from glob import glob
import matplotlib.pyplot as plt
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
from scipy.stats import linregress


create_summary_files = True

# Watersheds definition
watersheds = {'CAT': 22,
              'EBR': 23,
              'JUC': 16,
              'BAL': 8,
              'SEG': 7,
              'SUR': 21}

# Load the watershed mask
ws_mask = xr.open_dataset('watershed_mask.nc')


if create_summary_files:
    # Gather input files
    filesin = sorted(glob('./era5_daily_col_z500_????.nc'))
    
    # Define the number of files and years
    nfiles = len(filesin)

    # Initialize rolling dictionaries
    rolling_wpr_trends = {watershed: np.zeros(5) for watershed in watersheds.keys()}
    rolling_wpr_max_trends = {watershed: np.zeros(5) for watershed in watersheds.keys()}
    rolling_wpr_maxmean_trends = {watershed: np.zeros(5) for watershed in watersheds.keys()}
    rolling_col_trends = np.zeros(5)

    # Processing files
    for nf, filein in enumerate(tqdm(filesin, desc="Processing items")):
        tqdm.write(Fore.GREEN + f'Processing file {filein} ({nf+1}/{nfiles})')
        fin = xr.open_dataset(filein)
        
        # Extract COL objects
        unique_values = np.unique(fin.col_objects.values)
        col = unique_values[unique_values != 0]
        import pdb; pdb.set_trace()  # fmt: skip
        # Update rolling trends
        rolling_col_trends[nf % 5] = len(col)

        # Process COL days
        try:
            col_days = fin.where(fin.col_objects != 0, drop=True)
        except:
            continue
        pr_col_days = fin.pr.sel(time=col_days.time)
        pr_max_col_days = fin.pr_max.sel(time=col_days.time)

        # Compute trends for each watershed
        for watershed in watersheds.keys():
            rolling_wpr_trends[watershed][nf % 5] = pr_col_days.where(ws_mask.region_mask == watersheds[watershed]).sum().values
            rolling_wpr_maxmean_trends[watershed][nf % 5] = pr_max_col_days.where(ws_mask.region_mask == watersheds[watershed]).mean().values
            rolling_wpr_max_trends[watershed][nf % 5] = pr_max_col_days.where(ws_mask.region_mask == watersheds[watershed]).max().values

        # Save every 5 years
        if (nf + 1) % 5 == 0 or nf == nfiles - 1:  # Save after every 5 years or at the end
            start_year = 1940 + nf - (nf % 5)
            end_year = start_year + 4 if nf < nfiles - 1 else 1940 + nf
            output_file = f"wpr_trends_{start_year}_{end_year}.pkl"

            with open(output_file, 'wb') as f:
                pickle.dump({
                    'wpr_trends': rolling_wpr_trends,
                    'wpr_max_trends': rolling_wpr_max_trends,
                    'wpr_maxmean_trends': rolling_wpr_maxmean_trends,
                    'col_trends': rolling_col_trends
                }, f)

            tqdm.write(Style.BRIGHT + f"Saved results to {output_file}")

            # Reset rolling dictionaries for the next batch
            rolling_wpr_trends = {watershed: np.zeros(5) for watershed in watersheds.keys()}
            rolling_wpr_max_trends = {watershed: np.zeros(5) for watershed in watersheds.keys()}
            rolling_wpr_maxmean_trends = {watershed: np.zeros(5) for watershed in watersheds.keys()}
            rolling_col_trends = np.zeros(5)

