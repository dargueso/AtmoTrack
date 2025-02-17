import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy.stats import linregress
import pandas as pd
import json

# Watersheds definition
watersheds = {
    'CAT': 22,
    'EBR': 23,
    'JUC': 16,
    'BAL': 8,
    'SEG': 7,
    'SUR': 21
}

def load_data(file_path):
    """Load data from JSON file."""
    with open(file_path, 'r') as json_file:
        data = json.load(json_file)
    return data

if __name__ == "__main__":
    # Load data from the updated output file
    data = load_data("all_data.json")

    # Extract relevant data structures from JSON
    wpr_trends = data["wpr_trends"]
    total_rainfall_grid = np.array(data["total_rainfall_grid"])
    object_stats = data["object_stats"]
    max_rainfall_at_grid_point = data["max_rainfall_at_grid_point"]

    # Create a panel plot for the number of objects and their 10-year mean
    fig = plt.figure(figsize=(15, 10))
    gs = gridspec.GridSpec(2, 2, height_ratios=[1, 2])

    # Panel 1: Number of objects and 10-year mean
    ax1 = fig.add_subplot(gs[0, 0])
    object_counts = {ws: len(stats) for ws, stats in object_stats.items()}
    ten_year_mean = {ws: np.mean([obj["total_rainfall"] for obj in stats.values()]) if stats else 0
                     for ws, stats in object_stats.items()}
    ax1.bar(object_counts.keys(), object_counts.values(), alpha=0.7, label="Number of Objects")
    ax1.plot(list(ten_year_mean.keys()), list(ten_year_mean.values()), 'o-', label="10-Year Mean Rainfall", color='r')
    ax1.set_xlabel("Watersheds")
    ax1.set_ylabel("Count / Rainfall (mm)")
    ax1.set_title("Number of Objects and 10-Year Mean Rainfall")
    ax1.legend()
    ax1.grid(True)

    # Panel 2: Total rainfall by year for each watershed
    ax2 = fig.add_subplot(gs[0, 1])
    for watershed, trend in wpr_trends.items():
        years = np.arange(len(trend) // 12)
        annual_totals = [np.sum(trend[year * 12:(year + 1) * 12]) for year in years]
        ax2.plot(years, annual_totals, label=watershed)
    ax2.set_xlabel("Years")
    ax2.set_ylabel("Total Rainfall (mm)")
    ax2.set_title("Total Rainfall by Year for Each Watershed")
    ax2.legend()
    ax2.grid(True)

    # Panel 3: Maximum rainfall scatter plot
    ax3 = fig.add_subplot(gs[1, :])
    for watershed, stats in object_stats.items():
        max_rates = [obj["max_rate"] for obj in stats.values()]
        ax3.scatter([watershed] * len(max_rates), max_rates, label=f"{watershed} Max Rainfall Events", alpha=0.7)
    ax3.set_xlabel("Watersheds")
    ax3.set_ylabel("Max Rainfall Rate (mm/hour)")
    ax3.set_title("Maximum Rainfall Events Scatter Plot")
    ax3.legend()
    ax3.grid(True)

    # Save the panel plot
    plt.tight_layout()
    plt.savefig("combined_panel_plot.png")
    print("Combined panel plot saved as combined_panel_plot.png")
