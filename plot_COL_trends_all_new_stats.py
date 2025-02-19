import pandas as pd
import ast
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy.stats import linregress
import numpy as np
import xarray as xr

def safe_literal_eval(x):
    """
    Safely evaluate a string representation of a Python literal.
    Replaces bare 'nan' with 'None' to avoid errors.
    """
    if isinstance(x, str):
        x = x.replace('nan', 'None')
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
    df_main.columns = pd.MultiIndex.from_tuples([(col, '') for col in df_main.columns])
    
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
    df['year_val'] = df[('year', '')].astype(int)
    
    # Group by year.
    grouped = df.groupby('year_val')
    
    # Calculate the total number of objects (rows) per year.
    obj_count = grouped.size().rename(("object_count", ""))
    obj_count_df = obj_count.to_frame()
    # Make sure its columns are a MultiIndex.
    obj_count_df.columns = pd.MultiIndex.from_tuples([("object_count", "")])
    
    # Identify columns corresponding to rainfall from ws_sum_hires.
    rainfall_cols = [col for col in df.columns if col[0] == 'ws_sum_hires']
    # Sum the rainfall per watershed for each year.
    rainfall_sum = grouped[rainfall_cols].sum()

    # Do the same for ws_sum_lores
    rainfall_cols_lores = [col for col in df.columns if col[0] == 'ws_sum_lores']
    rainfall_sum_lores = grouped[rainfall_cols_lores].sum()
    
    # Merge the object counts and rainfall sums into a single DataFrame.
    annual_df = obj_count_df.join(rainfall_sum)
    annual_df = annual_df.join(rainfall_sum_lores)
    annual_df.index.name = 'year'
    return annual_df


def extract_max_ws_precip(df):
    """
    For each object (row), extract the maximum values from both:
      - ws_max_hires (hi-res precipitation) and the watershed (key) where it occurred.
      - ws_max_lores (lo-res precipitation) and the watershed (key) where it occurred.
    
    Assumes the DataFrame contains:
      - Scalar columns (e.g. ('object_id', '')).
      - Unpacked dictionary columns for ws_max_hires and ws_max_lores with a MultiIndex,
        where the first level is the column name and the second level is the watershed.
    """
    result = df[[('object_id', '')]].copy()  # Assuming you have an 'object_id' column.
    result['year'] = df[('year', '')].astype(int)
    result['month'] = df[('month', '')].astype(int)
    # For hi-res precipitation:
    ws_df_hires = df['ws_max_hires']
    result[('max_hires', '')] = ws_df_hires.max(axis=1)
    result[('max_hires_ws', '')] = ws_df_hires.idxmax(axis=1)
    
    # For lo-res precipitation:
    ws_df_lores = df['ws_max_lores']
    result[('max_lores', '')] = ws_df_lores.max(axis=1)
    result[('max_lores_ws', '')] = ws_df_lores.idxmax(axis=1)
    
    return result


def main():
    # === Load the CSV ===
    data = pd.read_csv("object_results_events.csv")
    
    # Ensure year and month are treated as integers.
    data['year'] = data['year'].astype(int)
    data['month'] = data['month'].astype(int)
    
    # === Unpack Dictionary Columns ===
    # List the names of columns that are stored as string representations of dictionaries.
    dict_cols = ['ws_max_lores', 'ws_sum_lores', 'ws_max_hires', 'ws_sum_hires']
    # Unpack these columns into a DataFrame with MultiIndex columns.
    data_multi = unpack_string_dict_columns(data, dict_cols)
    
    # === Create Annual Statistics DataFrame ===
    annual_df = create_annual_plot_df(data_multi)
    
   
    # Display the result.
    print("Annual Statistics DataFrame (for plotting):")
    print(annual_df.head())


    # --- Extract Maximum ws_max_hires for Each Object ---
    max_obj_pr = extract_max_ws_precip(data_multi)
    # Display the result.
    print("Extracted maximum ws_max_hires for each object:")
    print(max_obj_pr.head())


    # --- Load hires precip ---
    # annual_hires = pd.read_csv("annual_ws_pr_hires.csv.csv")
    # annual_lores = pd.read_csv("annual_ws_pr_lores.csv.csv")
    #import pdb; pdb.set_trace()  # fmt: skip
    # --- Load the watershed mask ---
    watersheds = {'CAT': 22, 'EBR': 23, 'JUC': 16, 'BAL': 8,
                'SEG': 7, 'SUR': 21}
    
    watersheds_list = list(watersheds.keys())
    n_watersheds = len(watersheds_list)

    ## PLOTTING 
    # Plot the annual statistics.

    colors = plt.cm.tab20(np.linspace(0, 1, len(watersheds_list)))  # Tab20 colormap for distinct categories
    palette = dict(zip(watersheds_list, colors))
  
    fig = plt.figure(figsize=(12, 15 + n_watersheds))
    gs = fig.add_gridspec(3, 1, height_ratios=(1,2,1), hspace=0.1)
    gs00 = gridspec.GridSpecFromSubplotSpec(6, 1,subplot_spec=gs[1,0],wspace=0,hspace=0)

    # Plot the total number of objects per year.
    ax = fig.add_subplot(gs[0, 0])
    ax.step(annual_df.index, annual_df[('object_count', '')],where='mid', color='black', label='Cut-off Lows')
    obj_count_rolling_10 = annual_df[('object_count', '')].rolling(window=10, min_periods=10,center=True).mean()
    obj_count_rolling_30 = annual_df[('object_count', '')].rolling(window=30, min_periods=30,center=True).mean()
    ax.plot(annual_df.index, obj_count_rolling_10, color='red', label='10-year Rolling Mean')
    ax.plot(annual_df.index, obj_count_rolling_30, color='blue', label='30-year Rolling Mean')
    ax.set_title('Total Number of Cut-off Lows per Year')
    ax.set_ylabel('Number of Cut-off Lows')
    ax.legend()
    ax.grid()

    # Plot the total rainfall per watershed.
    for i, ws in enumerate(watersheds_list):
        ax = fig.add_subplot(gs00[i, 0])
        ax.bar(annual_df.index, annual_df[('ws_sum_hires', ws)], color=colors[i], label=ws, width=0.8, align='center', zorder=1)
        ax.set_yticks([0,50,100,150])
        ax.set_ylim(0, 200)
        ax.set_ylabel(ws, rotation=0, labelpad=40, va='center')  # Add watershed labels

        if i == 0:
            ax.set_title("Watershed Precipitation (mm)")
        if i!=n_watersheds-1:
            ax.set_xticks([])
        
        #ax.set_ylabel('Total Rainfall (mm)')
        ax.legend()
        ax.grid()

        slope, intercept, r_value, p_value, std_err = linregress(annual_df.index, annual_df[('ws_sum_lores', ws)])
        trend_line = slope * np.array(annual_df.index) + intercept
        ax.plot(annual_df.index, trend_line, color='black', linestyle='dotted', label='Linear Trend')
        trend_text = f"Trend: {slope*10:.2f} mm/decade"
        ax.text(0.95, 0.85, trend_text, transform=ax.transAxes, fontsize=10, color='black',
                verticalalignment='top', horizontalalignment='right',bbox=dict(facecolor='white', alpha=0.8, edgecolor='none'))



    ax3 = fig.add_subplot(gs[2, 0])
    # Plot scatter points for each watershed.
    for ws in watersheds_list:
        subset = max_obj_pr[max_obj_pr['max_hires_ws'] == ws]
        ax3.scatter(subset['year'], subset['max_hires'], color=palette[ws], alpha=0.7,label=ws, s=100)

    ax3.set_title("Maximum Precipitation per Object")
    ax3.set_ylabel("Maximum Precipitation (mm)")

    plt.savefig("COL_stats_watersheds.png")
    import pdb; pdb.set_trace()  # fmt: skip
if __name__ == '__main__':
    main()
