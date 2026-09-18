import ast
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import linregress, t

import atmotrack_config as cfg  # noqa: E402


def add_trend_with_ci(ax, years, data, scale_factor=10, color="k", label=""):
    """Compute linear trend with 95% CI and add to ax."""
    x = np.array(years, dtype=float)
    y = np.array(data, dtype=float)
    mask = ~np.isnan(y)
    x, y = x[mask], y[mask]
    slope, intercept, _, _, std_err = linregress(x, y)
    trend_line = slope * x + intercept
    n = len(x)
    s_err = np.sqrt(np.sum((y - trend_line) ** 2) / (n - 2))
    t_val = t.ppf(0.975, n - 2)
    mean_x = np.mean(x)
    ssx = np.sum((x - mean_x) ** 2)
    se_pred = s_err * np.sqrt(1 / n + (x - mean_x) ** 2 / ssx)
    ci = t_val * se_pred
    slope_err = t.ppf(0.975, n - 2) * std_err
    ax.plot(x, trend_line, color=color, lw=1.5, linestyle="dotted", label="Linear Trend")
    ax.fill_between(
        x,
        trend_line - ci,
        trend_line + ci,
        alpha=0.3,
        color="grey",
        label="95% Confidence Interval",
    )
    ax.text(
        0.95,
        0.85,
        f"Trend: {slope * scale_factor:.2f} ± {slope_err * scale_factor:.2f} /decade (95% CI)",
        transform=ax.transAxes,
        fontsize=10,
        color="black",
        verticalalignment="top",
        horizontalalignment="right",
        bbox=dict(facecolor="white", alpha=0.8, edgecolor="none"),
    )
    return slope, slope_err


def safe_literal_eval(x):
    """
    Safely evaluate a string representation of a Python literal.
    Replaces bare 'nan' with 'None' to avoid errors.
    """
    if isinstance(x, str):
        x = x.replace("nan", "None")
        try:
            return ast.literal_eval(x)
        except Exception as e:
            print("Error evaluating:", x, "Error:", e)
            return x
    return x


def unpack_string_dict_columns(df, dict_cols):
    """
    Unpack one or more columns that contain dictionaries as strings.

    For each column in dict_cols:
      - Convert the string to a dictionary using safe_literal_eval.
      - Create a DataFrame from the list of dictionaries.
      - Rename the new columns with a MultiIndex: top level is the original column name,
        and second level is the key from the dictionary.

    The remaining (scalar) columns are also converted to a MultiIndex with an empty
    second level. Finally, all parts are concatenated.
    """
    df = df.copy()
    new_frames = []
    for col in dict_cols:
        # Evaluate the string representation into a dictionary
        df[col] = df[col].apply(safe_literal_eval)
        # Expand the dictionary into its own DataFrame (one dict per row)
        dict_df = pd.DataFrame(df[col].tolist())
        # Create a MultiIndex for these new columns:
        dict_df.columns = pd.MultiIndex.from_product([[col], dict_df.columns])
        new_frames.append(dict_df)

    # Drop the original dictionary columns from the DataFrame
    df_main = df.drop(columns=dict_cols)
    # Convert remaining scalar columns to MultiIndex (using an empty string for the second level)
    df_main.columns = pd.MultiIndex.from_tuples([(col, "") for col in df_main.columns])

    # Concatenate the scalar columns with the expanded dictionary columns
    df_new = pd.concat([df_main] + new_frames, axis=1)
    # Optionally, sort columns by the first level for clarity
    df_new = df_new.sort_index(axis=1, level=0)
    return df_new


def create_annual_plot_df(df):
    """
    Create an annual statistics DataFrame for plotting.

    Assumes the input DataFrame has MultiIndex columns with:
      - A scalar column for 'year' stored as ('year','')
      - Dictionary columns (unpacked) for ws_sum_hires stored as ('ws_sum_hires', watershed_name)

    The resulting DataFrame (indexed by year) will contain:
      - A column for the total number of objects per year (as ('object_count',''))
      - One column per watershed for the total rainfall (ws_sum_hires) per year.
    """
    df = df.copy()
    # Create a new column for grouping based on the year.
    # Here, ('year', '') is the original year column.
    df["year_val"] = df[("year", "")].astype(int)

    # Group by year.
    grouped = df.groupby("year_val")

    # Calculate the total number of objects (rows) per year.
    obj_count = grouped.size().rename(("object_count", ""))
    obj_count_df = obj_count.to_frame()
    # Make sure its columns are a MultiIndex.
    obj_count_df.columns = pd.MultiIndex.from_tuples([("object_count", "")])

    # Identify columns corresponding to rainfall from ws_sum_hires.
    rainfall_cols = [col for col in df.columns if col[0] == "ws_sum_hires"]
    # Sum the rainfall per watershed for each year.
    rainfall_sum = grouped[rainfall_cols].sum()

    # Do the same for ws_sum_lores
    rainfall_cols_lores = [col for col in df.columns if col[0] == "ws_sum_lores"]
    rainfall_sum_lores = grouped[rainfall_cols_lores].sum()

    # Merge the object counts and rainfall sums into a single DataFrame.
    annual_df = obj_count_df.join(rainfall_sum)
    annual_df = annual_df.join(rainfall_sum_lores)
    annual_df.index.name = "year"
    return annual_df


def main():
    plots_dir = cfg.plots_dir
    os.makedirs(plots_dir, exist_ok=True)

    stat_files = sorted(glob.glob(f"{cfg.stats_dir}/events_stats_????.csv"))
    if not stat_files:
        sys.exit(
            f"No events_stats_????.csv files found in {os.path.abspath(cfg.stats_dir)}\n"
            "Run calc_COL_stats_all_watersheds.py first, or set stats_dir in config.toml."
        )

    dfs = []
    for stat_fin in stat_files:
        df = pd.read_csv(stat_fin)
        dfs.append(df)

    data = pd.concat(dfs, ignore_index=True)

    # Ensure year and month are treated as integers.
    data["year"] = data["year"].astype(int)
    data["month"] = data["month"].astype(int)

    # === Unpack Dictionary Columns ===
    # List the names of columns that are stored as string representations of dictionaries.
    dict_cols = [
        "max_precip_ws_lores",
        "max_precip_ws_hires",
        "max_sum_precip_ws_lores",
        "max_sum_precip_ws_hires",
        "mean_precip_ws_lores",
        "mean_precip_ws_hires",
        "sum_precip_ws_lores",
        "sum_precip_ws_hires",
    ]

    # Unpack these columns into a DataFrame with MultiIndex columns.
    data_multi = unpack_string_dict_columns(data, dict_cols)

    # === Create Annual Statistics DataFrame ===
    annual_df = create_annual_plot_df(data_multi)
    years = annual_df.index

    # Display the result.
    print("Annual Statistics DataFrame (for plotting):")
    print(annual_df.head())
    # --- Extract columns --- #

    # --- Load hires precip ---
    # annual_hires = pd.read_csv("annual_ws_pr_hires.csv.csv")
    # annual_lores = pd.read_csv("annual_ws_pr_lores.csv.csv")
    # --- Load the watershed mask ---
    watersheds = {"CAT": 22, "EBR": 23, "JUC": 16, "BAL": 8, "SEG": 7, "SUR": 21, "MED": 25}

    watersheds_list = list(watersheds.keys())
    n_watersheds = len(watersheds_list)

    #####################################################################
    #####################################################################

    ## PLOTS ##

    colors = plt.cm.tab20(
        np.linspace(0, 1, len(watersheds_list))
    )  # Tab20 colormap for distinct categories
    palette = dict(zip(watersheds_list, colors))

    ## Annual count of Cut-off lows
    fig = plt.figure(figsize=(15, 5))
    ax = fig.add_subplot()
    ax.step(
        annual_df.index,
        annual_df[("object_count", "")],
        where="mid",
        color="black",
        label="Cut-off Lows",
    )
    obj_count_rolling_10 = (
        annual_df[("object_count", "")].rolling(window=10, min_periods=10, center=True).mean()
    )
    obj_count_rolling_30 = (
        annual_df[("object_count", "")].rolling(window=30, min_periods=30, center=True).mean()
    )
    ax.plot(annual_df.index, obj_count_rolling_10, color="red", label="10-year Rolling Mean")
    ax.plot(annual_df.index, obj_count_rolling_30, color="blue", label="30-year Rolling Mean")
    add_trend_with_ci(ax, annual_df.index, annual_df[("object_count", "")].values, scale_factor=10)

    ax.set_title("Total Number of Cut-off Lows per Year")
    ax.set_ylabel("Number of Cut-off Lows")
    ax.legend()
    ax.grid()
    fig.savefig(os.path.join(plots_dir, "Annual_count_COL.png"))
    plt.close()

    #####################################################################
    #####################################################################

    ## PLOTING FOR LO-RES DATA
    ## Precipitation maximum for each Cut-off low ##

    df = data_multi.loc[:, ["year", "month", "object_id", "max_precip_ws_lores"]]
    # df = df.drop([('max_precip_ws_lores', 'MED')], axis=1)
    df["max_value"] = df["max_precip_ws_lores"].max(axis=1)
    df["ws"] = df["max_precip_ws_lores"].idxmax(axis=1)
    fig = plt.figure(figsize=(15, 5))
    ax = fig.add_subplot()
    for ws in watersheds_list:
        subset = df[df["ws"] == ws]
        ax.scatter(
            subset["year"], subset["max_value"], color=palette[ws], alpha=0.7, label=ws, s=100
        )
    add_trend_with_ci(ax, df["year"], df["max_value"], scale_factor=10)
    ax.set_title("Maximum Precipitation per COL")
    ax.set_ylabel("Maximum Precipitation (mm)")
    ax.set_xlabel("Year")
    ax.set_xlim(1939, 2026)
    ax.set_ylim(-1, 40)
    ax.legend(loc="upper left", bbox_to_anchor=(1, 1))  # Moves legend outside the plot
    ax.grid()
    fig.savefig(os.path.join(plots_dir, "Max_precip_per_COL.png"))

    ## Precipitation maximum for each Cut-off low (NO MEDITERRANEAN) ##

    df = data_multi.loc[:, ["year", "month", "object_id", "max_precip_ws_lores"]]
    df = df.drop([("max_precip_ws_lores", "MED")], axis=1)
    df["max_value"] = df["max_precip_ws_lores"].max(axis=1)
    df["ws"] = df["max_precip_ws_lores"].idxmax(axis=1)
    fig = plt.figure(figsize=(15, 5))
    ax = fig.add_subplot()
    for ws in watersheds_list:
        subset = df[df["ws"] == ws]
        ax.scatter(
            subset["year"], subset["max_value"], color=palette[ws], alpha=0.7, label=ws, s=100
        )
    add_trend_with_ci(ax, df["year"], df["max_value"], scale_factor=10)
    ax.set_title("Maximum Precipitation per COL")
    ax.set_ylabel("Maximum Precipitation (mm)")
    ax.set_xlabel("Year")
    ax.set_xlim(1939, 2026)
    ax.set_ylim(-1, 40)
    ax.legend(loc="upper left", bbox_to_anchor=(1, 1))  # Moves legend outside the plot
    ax.grid()
    fig.savefig(os.path.join(plots_dir, "Max_precip_per_COL_NoMED.png"))

    ## Plotting total precipitation per watershed and year

    df = data_multi.loc[:, ["year", "month", "object_id", "max_sum_precip_ws_lores"]]
    df_yearly_max = df.drop([("object_id",)], axis=1).groupby("year").max()

    fig = plt.figure(figsize=(12, 12))
    gs = fig.add_gridspec(7, 1, hspace=0)
    for i, ws in enumerate(watersheds_list):
        dataplot = df_yearly_max[("max_sum_precip_ws_lores", ws)]
        ax = fig.add_subplot(gs[i, 0])
        ax.bar(
            dataplot.index,
            dataplot.values,
            color=colors[i],
            label=ws,
            width=0.8,
            align="center",
            zorder=1,
        )
        ax.set_yticks([0, 100, 200, 300])
        ax.set_ylim(0, 350)
        ax.set_xlim(1939, 2026)
        # ax.set_ylabel(ws, rotation=0, labelpad=40, va='center')  # Add watershed labels
        ax.set_ylabel("Precipitation (mm)")

        if i == 0:
            ax.set_title("Maximum Accum. Precipitation from one COL (mm/grid_point)")
        if i != n_watersheds - 1:
            ax.set_xticklabels([])

        # ax.set_ylabel('Total Rainfall (mm)')
        ax.legend(loc="upper left")
        ax.grid()

        add_trend_with_ci(ax, dataplot.index, dataplot.values, scale_factor=10)

    fig.savefig(os.path.join(plots_dir, "Max_Acc_precip_from_one_COL.png"))

    ## Plotting mean precipitation per watershed, year and COL
    df = data_multi.loc[:, ["year", "month", "object_id", "mean_precip_ws_lores"]]
    df_yearly_sum = df.drop([("object_id",)], axis=1).groupby("year").mean()

    fig = plt.figure(figsize=(12, 12))
    gs = fig.add_gridspec(7, 1, hspace=0)
    for i, ws in enumerate(watersheds_list):
        dataplot = df_yearly_sum[("mean_precip_ws_lores", ws)]
        ax = fig.add_subplot(gs[i, 0])
        ax.bar(
            dataplot.index,
            dataplot.values,
            color=colors[i],
            label=ws,
            width=0.8,
            align="center",
            zorder=1,
        )
        ax.set_yticks([0, 10, 20, 30])
        ax.set_ylim(0, 30)
        ax.set_xlim(1939, 2026)
        ax.set_ylabel("Precipitation (mm)")

        if i == 0:
            ax.set_title("Watershed Precipitation from COLs (mm/grid_point/COL)")
        if i != n_watersheds - 1:
            ax.set_xticklabels([])

        # ax.set_ylabel('Total Rainfall (mm)')
        ax.legend(loc="upper left")
        ax.grid()

        add_trend_with_ci(ax, dataplot.index, dataplot.values, scale_factor=10)
    fig.savefig(os.path.join(plots_dir, "Annual_precip_from_COLs.png"))

    #####################################################################
    #####################################################################
    # PLOTTING HIRES RESULTS
    # REMOVING MED BECAUSE HIRES IS DEFINED ONLY OVER LAND.

    watersheds = {"CAT": 22, "EBR": 23, "JUC": 16, "BAL": 8, "SEG": 7, "SUR": 21}

    watersheds_list = list(watersheds.keys())
    n_watersheds = len(watersheds_list)

    ## Precipitation maximum for each Cut-off low ##

    df = data_multi.loc[:, ["year", "month", "object_id", "max_precip_ws_hires"]]
    # df = df.drop([('max_precip_ws_hires', 'MED')], axis=1)
    # ERA5-Land only starts in 1950, so earlier COLs have no hi-res precipitation
    # in any watershed; idxmax raises on such all-NaN rows (pandas >= 2.1).
    df = df[df["max_precip_ws_hires"].notna().any(axis=1)].copy()
    df["max_value"] = df["max_precip_ws_hires"].max(axis=1)
    df["ws"] = df["max_precip_ws_hires"].idxmax(axis=1, skipna=True)
    df = df.dropna()
    fig = plt.figure(figsize=(15, 5))
    ax = fig.add_subplot()
    for ws in watersheds_list:
        subset = df[df["ws"] == ws]
        ax.scatter(
            subset["year"], subset["max_value"], color=palette[ws], alpha=0.7, label=ws, s=100
        )
    add_trend_with_ci(ax, df["year"], df["max_value"], scale_factor=10)
    ax.set_title("Maximum Precipitation per COL (Hi-res)")
    ax.set_ylabel("Maximum Precipitation (mm)")
    ax.set_xlabel("Year")
    ax.set_xlim(1939, 2026)
    ax.set_ylim(-1, 40)
    ax.legend(loc="upper left", bbox_to_anchor=(1, 1))  # Moves legend outside the plot
    ax.grid()
    fig.savefig(os.path.join(plots_dir, "Max_precip_per_COL_hires.png"))

    ## Plotting maximum accumulated precipitation per watershed and year

    df = data_multi.loc[:, ["year", "month", "object_id", "max_sum_precip_ws_hires"]]
    # df = df.drop([('max_sum_precip_ws_hires', 'MED')], axis=1)
    df_yearly_max = df.drop([("object_id",)], axis=1).groupby("year").max()
    df_yearly_max = df_yearly_max.dropna()
    fig = plt.figure(figsize=(12, 12))
    gs = fig.add_gridspec(7, 1, hspace=0)
    for i, ws in enumerate(watersheds_list):
        dataplot = df_yearly_max[("max_sum_precip_ws_hires", ws)]
        ax = fig.add_subplot(gs[i, 0])
        ax.bar(
            dataplot.index,
            dataplot.values,
            color=colors[i],
            label=ws,
            width=0.8,
            align="center",
            zorder=1,
        )
        ax.set_yticks([0, 100, 200, 300])
        ax.set_ylim(0, 350)
        ax.set_xlim(1939, 2026)
        # ax.set_ylabel(ws, rotation=0, labelpad=40, va='center')  # Add watershed labels
        ax.set_ylabel("Precipitation (mm)")

        if i == 0:
            ax.set_title("Maximum Accum. Precipitation from one COL (mm/grid_point) Hi-res")
        if i != n_watersheds - 1:
            ax.set_xticklabels([])

        # ax.set_ylabel('Total Rainfall (mm)')
        ax.legend(loc="upper left")
        ax.grid()

        add_trend_with_ci(ax, dataplot.index, dataplot.values, scale_factor=10)

    fig.savefig(os.path.join(plots_dir, "Max_Acc_precip_from_one_COL_hires.png"))

    ## Plotting mean precipitation per watershed, year and COL
    df = data_multi.loc[:, ["year", "month", "object_id", "mean_precip_ws_hires"]]
    # df = df.drop([('mean_precip_ws_hires', 'MED')], axis=1)
    df_yearly_sum = df.drop([("object_id",)], axis=1).groupby("year").mean()
    df_yearly_sum = df_yearly_sum.dropna()

    fig = plt.figure(figsize=(12, 12))
    gs = fig.add_gridspec(7, 1, hspace=0)
    for i, ws in enumerate(watersheds_list):
        dataplot = df_yearly_sum[("mean_precip_ws_hires", ws)]
        ax = fig.add_subplot(gs[i, 0])
        ax.bar(
            dataplot.index,
            dataplot.values,
            color=colors[i],
            label=ws,
            width=0.8,
            align="center",
            zorder=1,
        )
        ax.set_yticks([0, 10, 20, 30])
        ax.set_ylim(0, 30)
        ax.set_xlim(1939, 2026)
        ax.set_ylabel("Precipitation (mm)")

        if i == 0:
            ax.set_title("Watershed Precipitation from COLs (mm/grid_point/COL) Hi-res")
        if i != n_watersheds - 1:
            ax.set_xticklabels([])

        # ax.set_ylabel('Total Rainfall (mm)')
        ax.legend(loc="upper left")
        ax.grid()

        add_trend_with_ci(ax, dataplot.index, dataplot.values, scale_factor=10)

    fig.savefig(os.path.join(plots_dir, "Annual_precip_from_COLs_hires.png"))

    #####################################################################
    #####################################################################

    # Make some plots of seasonality
    df = data_multi.loc[:, ["year", "month", "object_id", "duration", "max_sum_precip_ws_hires"]]
    count_per_month = df.drop([("object_id",)], axis=1).groupby("month").count().duration / len(
        years
    )
    df_months = df.drop([("object_id",), ("duration",)], axis=1).groupby("month").max()
    df_months["ncols"] = count_per_month
    df_months.index = pd.to_datetime(df_months.index, format="%m")
    fig = plt.figure(figsize=(15, 5))
    ax = fig.add_subplot()
    ax.bar(
        df_months.index,
        df_months.ncols.values,
        color="grey",
        label=ws,
        width=10,
        align="center",
        zorder=1,
    )
    ax.set_ylim(0, 3)
    ax.set_ylabel("Number of COLs")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.set_title("Seasonality of COLs - Average number of COLs per month")
    ax.grid()
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "COL_count_seasonality.png"))


if __name__ == "__main__":
    main()
