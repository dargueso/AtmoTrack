import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import seaborn as sns
import ast
import numpy as np

# ============================
# Load Yearly and Event Data
# ============================
# Yearly aggregated data: expected to have columns 'year', 'event_count', 'event_count_ma', and
# columns starting with "wpr_trends_" for each watershed.
df_yearly = pd.read_csv('object_results_yearly_trends.csv')
df_yearly['year'] = df_yearly['year'].astype(int)
df_yearly = df_yearly.sort_values('year')

# Event-level data: we need to extract the maximum hi-res rainfall and its watershed.
df_events = pd.read_csv('object_results_events.csv')

# Convert the 'ws_max_hires' column from its string representation to a dictionary.
def parse_dict(x):
    try:
        return ast.literal_eval(x)
    except Exception:
        return {}
df_events['ws_max_hires'] = df_events['ws_max_hires'].apply(parse_dict)

# For each event, get the maximum hi-res rainfall and the corresponding watershed.
def get_max_hires(row):
    d = row['ws_max_hires']
    if not d or not isinstance(d, dict):
        return pd.Series({"max_hires": np.nan, "max_hires_watershed": None})
    # Remove keys with None values
    d = {k: v for k, v in d.items() if v is not None}
    if not d:
        return pd.Series({"max_hires": np.nan, "max_hires_watershed": None})
    max_key = max(d, key=lambda k: d[k])
    return pd.Series({"max_hires": d[max_key], "max_hires_watershed": max_key})
df_events[['max_hires', 'max_hires_watershed']] = df_events.apply(get_max_hires, axis=1)
df_events['time'] = pd.to_datetime(df_events['time'])

# ============================
# Create the Figure with GridSpec
# ============================
fig = plt.figure(figsize=(16, 18))
# Create an outer GridSpec with 3 rows:
outer_gs = gridspec.GridSpec(nrows=3, ncols=1, height_ratios=[1, 1, 1], hspace=0.3)

# ----- Panel 1: Event Count Step Plot -----
ax0 = fig.add_subplot(outer_gs[0])
ax0.step(df_yearly['year'], df_yearly['event_count'], where='mid', color='gray', label='Annual COL Count')
ax0.plot(df_yearly['year'], df_yearly['event_count_ma'], color='blue', linewidth=2, label='10-year MA')
ax0.set_title('Number of COL Events per Year')
ax0.set_xlabel('Year')
ax0.set_ylabel('Event Count')
ax0.legend()

# ----- Panel 2: 6 Subpanels for Bar Plots of Total Hi-Res Watershed Precipitation -----
# Assume the yearly file has columns like "wpr_trends_<watershed>".
# We'll use 6 panels. If there are more than 6, we take the first 6.
wtr_cols = [col for col in df_yearly.columns if col.startswith('wpr_trends_')]
if len(wtr_cols) > 6:
    wtr_cols = wtr_cols[:6]

# Create a nested GridSpec within the second row.
gs2 = gridspec.GridSpecFromSubplotSpec(1, 6, subplot_spec=outer_gs[1], wspace=0, hspace=0)
axes2 = [fig.add_subplot(gs2[i]) for i in range(6)]

for ax, col in zip(axes2, wtr_cols):
    ax.bar(df_yearly['year'], df_yearly[col], color='skyblue')
    # Use the watershed name by stripping the prefix:
    ws_label = col.replace('wpr_trends_', '')
    ax.set_title(ws_label, fontsize=10)
    ax.set_xlabel('Year', fontsize=9)
    ax.tick_params(axis='both', labelsize=8)
# Set a ylabel only for the leftmost panel.
axes2[0].set_ylabel('Total Accumulated Precipitation (mm)', fontsize=10)

# ----- Panel 3: Scatter Plot of Maximum Hi-Res Rainfall per Event -----
ax3 = fig.add_subplot(outer_gs[2])
sns.scatterplot(data=df_events, x='time', y='max_hires', hue='max_hires_watershed',
                palette='Set1', s=80, ax=ax3)
ax3.set_title('Maximum Hi-Res Watershed Rainfall per COL Event')
ax3.set_xlabel('Event Time')
ax3.set_ylabel('Max Hi-Res Rainfall (mm)')
ax3.legend(title='Watershed', loc='upper left', fontsize=9, title_fontsize=10)

plt.tight_layout()
plt.savefig('COL_trends_all_panels.png')
