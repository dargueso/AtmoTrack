# AtmoTrack

Atmospheric system tracking from ERA5 reanalysis data (1940–2024).

Tracks Cut-Off Lows (COL), upper-level and surface cyclones/anticyclones,
Mesoscale Convective Systems (MCS), and fronts.

---

## Requirements

Python ≥ 3.11 (uses built-in `tomllib`; for 3.9/3.10 install `tomli`).

```bash
pip install -r requirements.txt
```

---

## Configuration

All thresholds, domain settings, and file paths are set in **`config.toml`**.
Edit this file before running any script — no source code changes needed.

Key path settings (under `[paths]`):

| Key | Description |
|---|---|
| `data_era5` | Directory with ERA5 input NetCDF files |
| `data_tracking` | Output directory for tracking NetCDF files |
| `watershed_mask` | Path to `watershed_mask_medsea.nc` |
| `hires_pr_pattern` | Glob pattern for ERA5-Land precipitation files |
| `stats_dir` | Output directory for per-event statistics CSVs |

---

## Expected input data layout

```
data_era5/
  era5_daily_500hPa_YYYY.nc   # geopotential (z) at 500 hPa
  era5_daily_200hPa_YYYY.nc   # u-wind at 200 hPa
  era5_daily_850hPa_YYYY.nc   # t, u, v at 850 hPa
  era5_daily_PR_YYYY.nc       # total precipitation
  era5_daily_SLP_YYYY.nc      # mean sea-level pressure (msl, Pa)
```

All files use `valid_time` as the time dimension and `latitude`/`longitude`
as coordinate variables (standard ERA5 CDS download format).

---

## Entry scripts

| Script | What it does |
|---|---|
| `COL_tracking_ERA5.py` | Detect and track Cut-Off Lows (COL) from 500 hPa Z anomalies |
| `CY_ACY500_tracking_ERA5.py` | Track upper-level cyclones/anticyclones from 500 hPa Z |
| `SLP_tracking_ERA5.py` | Track surface cyclones/anticyclones from SLP |
| `calc_COL_stats_all_watersheds.py` | Aggregate per-COL precipitation stats by watershed |
| `plot_COL_stats.py` | Plot annual statistics and trends |

Run any script from the project root directory, e.g.:

```bash
python COL_tracking_ERA5.py
```

All scripts use `n_jobs=-1` (all available CPU cores via `joblib.Parallel`).
To limit parallelism, set `n_jobs=N` inside the script's `main()` function.

---

## Output

- **Tracking NetCDF** (in `data_tracking/`): one file per year containing
  labeled object arrays (`col_objects`, `cy_z500_objects`, `cy_slp_objects`, …).
- **Statistics CSVs** (in `events_stats/`): one file per year with per-COL
  precipitation totals broken down by watershed.
- **Plots** (current directory): PNG files for annual counts, precipitation
  maxima, trends, and seasonality.

---

## Core library

`tracking_functions.py` contains all detection, labeling, and tracking
algorithms. Import individual functions from it rather than running it directly.

`utils.py` provides the shared logger and ANSI color helpers used by all scripts.

`constants.py` holds physical constants (gravity, Earth radius, etc.).

---

## Watershed codes (`watershed_mask_medsea.nc`)

| Code | Watershed |
|---|---|
| 22 | CAT (Catalonia) |
| 23 | EBR (Ebro) |
| 16 | JUC (Júcar) |
| 8  | BAL (Balearic) |
| 7  | SEG (Segura) |
| 21 | SUR (Sur) |
| 25 | MED (Mediterranean open) |
