import pickle
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy.stats import linregress
import pandas as pd
from itertools import chain
import xarray as xr
from colorama import Fore, Style

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

# Load the combined results
with open("combined_results_all_years.pkl", "rb") as f:
    data = pickle.load(f)

import pdb; pdb.set_trace()  # fmt: skip
# Extract monthly trends and object maxima
monthly_trends = data["monthly_trends"]
object_stats = data["object_stats"]

wpr_trends = monthly_trends["wpr_trends"]
wpr_max_trends = monthly_trends["wpr_max_trends"]
wpr_maxmean_trends = monthly_trends["wpr_maxmean_trends"]
col_trends = np.array(monthly_trends["col_trends"])
col_objects_id = monthly_trends["col_objects_id"]


# Generate monthly time axis
start_year = 1940
total_months = len(col_trends)
date_index = pd.date_range(start=f"{start_year}-01", periods=total_months, freq="ME")
years = np.arange(start_year, start_year + total_months // 12)
months = np.tile(np.arange(1, 13), len(years))
time_axis = np.array([f"{y}-{m:02d}" for y, m in zip(years.repeat(12), months)])


# Create DataFrames
col_trends_df = pd.DataFrame({"col_trends": col_trends}, index=date_index)
wpr_trends_df = pd.DataFrame(wpr_trends, index=date_index)
wpr_max_trends_df = pd.DataFrame(wpr_max_trends, index=date_index)
wpr_maxmean_trends_df = pd.DataFrame(wpr_maxmean_trends, index=date_index)
col_objects_id_df = pd.DataFrame({"col_objects_id": col_objects_id}, index=date_index)


# Create a DataFrame from the object_maxima
rows = []
for watershed, entries in object_stats.items():
    for entry in entries:
        rows.append({
            "watershed": watershed,
            "object_id": entry["object_id"],
            "year": entry["year"],
            "month": entry["month"],
            "max_pr_rate": entry["max_pr_rate"],
            "max_pr_accum": entry["max_pr_accum"],
            "ws_pr_accum": entry["ws_pr_accum"]
        })

# Convert the list of dictionaries into a pandas DataFrame
object_maxima_df = pd.DataFrame(rows)

# Add a datetime column for easier manipulation
object_maxima_df["date"] = pd.to_datetime(
    dict(year=object_maxima_df["year"], month=object_maxima_df["month"], day=1)
)

# Set the datetime as the index
object_maxima_df.set_index("date", inplace=True)


col_trends_df_year = col_trends_df.groupby(col_trends_df.index.year).sum()
wpr_trends_df_year = wpr_trends_df.groupby(wpr_trends_df.index.year).sum()
wpr_max_trends_df_year = wpr_max_trends_df.groupby(wpr_max_trends_df.index.year).max()

object_maxima_df['year'] = object_maxima_df.index.year
object_maxima_df['max_pr_accum'] = object_maxima_df['max_pr_accum'].apply(lambda x: x.item() if isinstance(x, np.ndarray) else x)
object_maxima_df_year = object_maxima_df.groupby(["year", "watershed"]).mean()

col_objects_id_df['year'] = col_objects_id_df.index.year
col_objects_id_df['month'] = col_objects_id_df.index.month


unique_object_count_by_year = (
    col_objects_id_df.groupby('year')['col_objects_id']
    .apply(lambda x: len(set(chain.from_iterable(x)) - {0}))
    .reset_index(name='unique_object_count')
)

unique_object_count_by_year.set_index("year", inplace=True)

#Calculating extreme rainfal stats


year_max_ws = object_maxima_df_year.reset_index()
year_max_nwindow_mean = np.zeros(len(year_max_ws.year.unique().tolist()))
year_max_nwindow_median = np.zeros(len(year_max_ws.year.unique().tolist()))
year_allmax_nwindow = year_max_ws.groupby('year').max().max_pr_accum.rolling(window=10, min_periods=10,center=True).mean()
years_window = year_max_ws.year.unique().tolist()
for i, year in enumerate(years_window):
    if year < years_window[0] + 5 or year > years_window[-1] - 4:
        year_max_nwindow_mean[i] = np.nan
        year_max_nwindow_median[i] = np.nan
    else:
        year_max_nwindow_mean[i] =  year_max_ws[(year_max_ws.year>=year-5) & (year_max_ws.year<year+5)].max_pr_accum.mean()
        year_max_nwindow_median[i] =  year_max_ws[(year_max_ws.year>year-5) & (year_max_ws.year<year+5)].max_pr_accum.median()

year_max_nwindow_stats = pd.DataFrame({'year': years_window, 'mmean': year_max_nwindow_mean, 'mmedian': year_max_nwindow_median})

# Normalize WPR trends for plotting


watersheds_list = list(wpr_trends.keys())
n_watersheds = len(watersheds_list)
normalized_wpr_trends = {ws: np.array(wpr_trends[ws]) / np.nanmax(wpr_trends[ws]) for ws in watersheds_list}

num_points_ws = {}
for ws in watersheds_list:
    num_points_ws[ws] = (ws_mask==watersheds[ws]).sum().region_mask.item()

# Plotting

colors = plt.cm.tab20(np.linspace(0, 1, len(watersheds_list)))  # Tab20 colormap for distinct categories


fig = plt.figure(figsize=(12, 15 + n_watersheds))
gs = fig.add_gridspec(3, 1, height_ratios=(1,2,1), hspace=0.1)
gs00 = gridspec.GridSpecFromSubplotSpec(6, 1,subplot_spec=gs[1,0],wspace=0,hspace=0)

# Panel 1: COL trends
ax1 = fig.add_subplot(gs[0, 0])
ax1.step(unique_object_count_by_year.index, unique_object_count_by_year, where="mid", color="black", linestyle="dotted", label="COL Trends")
col_trends_running_mean = unique_object_count_by_year["unique_object_count"].rolling(window=10, min_periods=10,center=True).mean()
ax1.plot(col_trends_running_mean.index, col_trends_running_mean, color="red", label="12-Month Running Mean")
ax1.set_title("COL Trends and 10-Year Running Mean")
ax1.set_ylabel("COL Count")
ax1.legend()
ax1.grid()


# Panels 2-n: WPR trends as contiguous bar plots
for i, ws in enumerate(watersheds_list):
    ax = fig.add_subplot(gs00[i,0], sharex=ax1)
    if i == 0:
        ax.set_title("Watershed Precipitation (mm/)")
    ax.bar(wpr_trends_df_year.index, wpr_trends_df_year[ws]/num_points_ws[ws], label=watershed, color=colors[i], width=0.8, align='center', zorder=1)
    ax.set_yticks([0,100,300,500])  # Remove y-axis ticks
    ax.set_ylim(0, 600)  # Set y-axis limits
    ax.set_ylabel(watershed, rotation=0, labelpad=40, va='center')  # Add watershed labels
    if i != n_watersheds - 1:
        ax.tick_params(axis='x', which='both', bottom=False, labelbottom=False)  # Hide x-axis labels for all but the last plot
    ax.grid(True)  # Remove gridlines for simplicity

    # Add linear trend
    slope, intercept, r_value, p_value, std_err = linregress(wpr_trends_df_year.index, wpr_trends_df_year[ws]/num_points_ws[ws])
    trend_line = slope * np.array(years) + intercept
    ax.plot(years, trend_line, color='black', linestyle='--', linewidth=2, label='Linear Trend')
    trend_text = f"Trend: {slope*10:.2f} mm/decade"
    ax.text(0.95, 0.85, trend_text, transform=ax.transAxes, fontsize=10,
            verticalalignment='top', horizontalalignment='right', bbox=dict(facecolor='white', alpha=0.8, edgecolor='none'))


# # Panel 2: Watershed WPR Trends
# ax2 = fig.add_subplot(gs[1, 0])

# for i, ws in enumerate(watersheds_list):
#     ax2.plot(wpr_trends_df_year.index, wpr_trends_df_year[ws], label=ws, color=colors[i])
# ax2.set_title("Watershed Normalized WPR Trends")
# ax2.set_xlabel("Time")
# ax2.set_ylabel("Normalized WPR")
# ax2.legend()
# ax2.grid()


# Panel 3: Object Maximum Precipitation

ax3 = fig.add_subplot(gs[2, 0])
for i, ws in enumerate(watersheds_list):
    max_pr_accum = object_maxima_df_year.xs(ws, level='watershed')['max_pr_accum']
    ax3.scatter(max_pr_accum.index, max_pr_accum, label=ws, alpha=0.7, color=colors[i])


ax3.plot(year_max_nwindow_stats.year, year_max_nwindow_stats.mmean, label="10-Year Running Mean (All Watersheds)", 
         color='red', linewidth=2, zorder=4)
ax3.plot(year_max_nwindow_stats.year, year_max_nwindow_stats.mmedian, label="10-Year Running Median (All Watersheds)", 
          color='blue', linewidth=2, zorder=4)

ax3.plot(year_allmax_nwindow.index, year_allmax_nwindow, label="10-Year Running Median (All Watersheds)", 
          color='blue', linewidth=2, zorder=4)
ax3.set_title("Maximum Precipitation per Object")
ax3.set_ylabel("Maximum Precipitation (mm)")
#ax3.legend()
ax3.grid()

# Save and show the plot
#plt.tight_layout()
plt.savefig("COL_WPR_Trends_Combined.png")
#plt.show()
