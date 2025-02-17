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

# Watersheds definition
watersheds = {'CAT': 22,
              'EBR': 23,
              'JUC': 16,
              'BAL': 8,
              'SEG': 7,
              'SUR': 21}

# Load the watershed mask
ws_mask = xr.open_dataset('watershed_mask.nc')


# File pattern to find saved files
file_pattern = "wpr_trends_????_????.pkl"

# Initialize empty dictionaries for concatenation
wpr_trends = {}
wpr_max_trends = {}
wpr_maxmean_trends = {}
col_trends_list = []

# Find all matching files and sort them
saved_files = sorted(glob(file_pattern))

# Process each file and concatenate data
for file in saved_files:
    with open(file, 'rb') as f:
        data = pickle.load(f)

        # Add data to col_trends
        col_trends_list.append(data['col_trends'])

        # Merge dictionary data
        for watershed in data['wpr_trends']:
            if watershed not in wpr_trends:
                wpr_trends[watershed] = []
                wpr_max_trends[watershed] = []
                wpr_maxmean_trends[watershed] = []

            wpr_trends[watershed].extend(data['wpr_trends'][watershed])
            wpr_max_trends[watershed].extend(data['wpr_max_trends'][watershed])
            wpr_maxmean_trends[watershed].extend(data['wpr_maxmean_trends'][watershed])

# Convert col_trends into a single array
col_trends = np.concatenate(col_trends_list)

import pdb; pdb.set_trace()  # fmt: skip

# Convert concatenated lists into numpy arrays for consistency
for watershed in wpr_trends:
    wpr_trends[watershed] = np.array(wpr_trends[watershed])
    wpr_max_trends[watershed] = np.array(wpr_max_trends[watershed])
    wpr_maxmean_trends[watershed] = np.array(wpr_maxmean_trends[watershed])

#####################################################################
#####################################################################


# Prepare for plotting
years = np.arange(1940, 1940 + len(col_trends))  # Years based on col_trends length
watersheds_list = list(wpr_trends.keys())

# Stack the wpr_trends values for plotting
num_points_ws = {}
for ws in watersheds_list:
    num_points_ws[ws] = (ws_mask==watersheds[ws]).sum().region_mask.item()


# Stack the wpr_trends values for plotting
wpr_stacked = np.stack([wpr_trends[ws]/num_points_ws[ws] for ws in watersheds_list])

# Calculate the 10-year running mean for col_trends
col_trends_running_mean = np.convolve(col_trends, np.ones(10) / 10, mode='valid')
mean_years = years[4:-5]  # Adjust years for running mean


# Calculate a combined maximum WPR trend for all watersheds
combined_max_trends = np.stack([wpr_max_trends[ws] for ws in watersheds_list])

# Calculate the 10-year running mean for the combined maximum trends

# Initialize an array to store the running mean
yr_for_window = combined_max_trends.shape[1]
combined_max_running_mean = np.full(yr_for_window, np.nan)  # Fill with NaN initially
combined_max_running_median = np.full(yr_for_window, np.nan)  # Fill with NaN initially

for i in range(5, yr_for_window - 5):  # Skip the first 5 and last 5 years
    start = i - 5
    end = i + 5 + 1  # +1 because slicing is exclusive at the end
    window = combined_max_trends[:, start:end]  # Take the window
    combined_max_running_mean[i] = np.mean(window)  # Calculate the mean over all locations and the window
    combined_max_running_median[i] = np.median(window)  # Calculate the median over all locations and the window



bar_colors = plt.cm.tab20(np.linspace(0, 1, len(watersheds_list)))  # Tab20 colormap for distinct categories

# Define the grid layout
n_watersheds = len(watersheds_list)
fig = plt.figure(figsize=(12, 15 + n_watersheds))

gs = fig.add_gridspec(3, 1, height_ratios=(1,2,1), hspace=0.1)
gs00 = gridspec.GridSpecFromSubplotSpec(6, 1,subplot_spec=gs[1,0],wspace=0,hspace=0)


# Panel 1: COL trends and running mean
ax1 = fig.add_subplot(gs[0, 0])
ax1.step(years, col_trends, where='mid', label="COL Trends", color='black', linestyle='dotted', linewidth=1.5, zorder=3)
ax1.plot(mean_years, col_trends_running_mean, label="10-Year Running Mean", color='red', linewidth=2, zorder=4)
ax1.set_ylabel("COL (Count)")
ax1.legend(loc="upper left")
ax1.grid(True)
ax1.set_title("COL Trends and 10-Year Running Mean")

# Panels 2-n: WPR trends as contiguous bar plots
for i, watershed in enumerate(watersheds_list):
    ax = fig.add_subplot(gs00[i,0], sharex=ax1)
    if i == 0:
        ax.set_title("Watershed Precipitation (mm/)")
    ax.bar(years, wpr_stacked[i], label=watershed, color=bar_colors[i], width=0.8, align='center', zorder=1)
    ax.set_yticks([0,100,300,500])  # Remove y-axis ticks
    ax.set_ylim(0, 600)  # Set y-axis limits
    ax.set_ylabel(watershed, rotation=0, labelpad=40, va='center')  # Add watershed labels
    if i != n_watersheds - 1:
        ax.tick_params(axis='x', which='both', bottom=False, labelbottom=False)  # Hide x-axis labels for all but the last plot
    ax.grid(True)  # Remove gridlines for simplicity

    # Add linear trend
    slope, intercept, r_value, p_value, std_err = linregress(years, wpr_stacked[i])
    trend_line = slope * np.array(years) + intercept
    ax.plot(years, trend_line, color='black', linestyle='--', linewidth=2, label='Linear Trend')
    trend_text = f"Trend: {slope*10:.2f} mm/decade"
    ax.text(0.95, 0.85, trend_text, transform=ax.transAxes, fontsize=10,
            verticalalignment='top', horizontalalignment='right', bbox=dict(facecolor='white', alpha=0.8, edgecolor='none'))

# Panel n+1: Scatter plot of maximum precipitation trends and running mean
ax3 = fig.add_subplot(gs[2, 0])
for i, watershed in enumerate(watersheds_list):
    ax3.scatter(years, wpr_max_trends[watershed], label=watershed, color=bar_colors[i], alpha=0.7)
ax3.plot(years, combined_max_running_mean, label="10-Year Running Mean (All Watersheds)", 
         color='red', linewidth=2, zorder=4)
ax3.plot(years, combined_max_running_median, label="10-Year Running Median (All Watersheds)", 
         color='blue', linewidth=2, zorder=4)

ax3.set_ylabel("Maximum WPR Trends (mm/hr)")
ax3.set_xlabel("Year")
ax3.legend(loc="upper left", title="Watersheds")
ax3.grid(True)
ax3.set_title("Maximum Watershed Precipitation with Running Mean")

# Adjust layout for proper spacing and save the figure
fig.subplots_adjust(top=0.95, bottom=0.05, left=0.1, right=0.9)
plt.savefig("COL_WPR_Trends_ContiguousBarPlots.png")

