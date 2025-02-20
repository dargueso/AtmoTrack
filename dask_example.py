from dask.distributed import Client
import xarray as xr
import time
from dask.diagnostics import ProgressBar

def main():
    # -----------------------------------------------------------
    # 1. Start a local Dask cluster and check the dashboard info
    # -----------------------------------------------------------
    
    # Create a local Dask client with custom settings.
    client = Client(n_workers=8, threads_per_worker=2, memory_limit='4GB')
    
    # Print client info and dashboard URL.
    print("Dask Client Information:")
    print(client)
    print("Dashboard Link:", client.dashboard_link)
    
    # Optionally, check connected workers.
    workers = client.scheduler_info()['workers']
    print("\nConnected workers:")
    for worker, info in workers.items():
        print(f"Worker: {worker}, Details: {info}")
    
    # -----------------------------------------------------------
    # 2. Open multiple NetCDF files with adapted chunking
    # -----------------------------------------------------------
    
    # Define the file pattern (each file contains one year).
    # Adjust the path and naming pattern to match your files.
    file_pattern = './era5_daily_col_z500_????.nc'
    
    # Open the files using xarray's open_mfdataset with adapted chunking.
    # Here we set 'time' to -1 so each file's time dimension is a single chunk
    # (handling differences between leap and non-leap years),
    # and chunk the spatial dimensions into smaller pieces.
    ds = xr.open_mfdataset(
        file_pattern,
        combine='by_coords',
        chunks={'time': -1, 'latitude': 261, 'longitude': 201}
    )
    
    print("\nOpened Dataset:")
    print(ds)
    print("\nDataset dimensions:", ds.dims)
    
    # Check the chunking structure of one variable (if available).
    for var in ds.data_vars:
        print(f"Variable '{var}' has chunks:", ds[var].data.chunks)
        break  # Just printing for the first variable
    
    # -----------------------------------------------------------
    # 3. Perform a sample computation to trigger Dask tasks
    # -----------------------------------------------------------
    
    # For example, compute the time mean for one of the variables.
    # Replace 'your_variable' with the actual variable name if needed.
    print("\nPerforming sample computation: calculating the time mean for the first data variable...")
    data_var = list(ds.data_vars)[0]  # Get the first variable in the dataset.
    time_mean = ds[data_var].mean(dim='time')
    
    # Use a Dask ProgressBar to monitor progress in the console.
    pbar = ProgressBar()
    pbar.register()

    # Measure the elapsed time.
    start_time = time.time()
    result = time_mean.compute()  # This triggers the computation using the Dask cluster.
    elapsed_time = time.time() - start_time

    print("\nSample computation complete.")
    print("Elapsed time: {:.2f} seconds".format(elapsed_time))
    
    # You can also inspect the result.
    print("\nResult:")
    print(result)

if __name__ == '__main__':
    main()

