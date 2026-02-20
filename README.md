# AtmoTrack

Atmospheric system tracking from ERA5 reanalysis data (1940–2024).

Tracks Cut-Off Lows (COL), upper-level and surface cyclones/anticyclones,
Mesoscale Convective Systems (MCS), and fronts.

---

## Citing AtmoTrack

If you use AtmoTrack in published research, please cite it:

> Argüeso, D. (2024). *AtmoTrack: Atmospheric system tracking from ERA5
> reanalysis data*. https://github.com/dargueso/AtmoTrack

A machine-readable citation is available in `CITATION.cff` (GitHub shows a
**"Cite this repository"** button in the sidebar that exports BibTeX, APA,
and other formats automatically).

---

## Requirements

Python ≥ 3.11 (uses built-in `tomllib`; for 3.9/3.10 install `tomli`).

### With conda (recommended)

```bash
conda env create -f environment.yml
conda activate atmotrack
```

### With pip

```bash
pip install -e ".[dev]"          # installs project + dev tools (pytest, ruff)
pip install -e ".[dev,download]" # also installs cdsapi for download_ERA5.py
pip install -e ".[dev,misc]"     # also installs cartopy/geopandas for Misc/ scripts
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

### Z500 smoothing method

The 500 hPa cyclone/anticyclone tracking supports two small-scale smoothing
methods, configured under `[cy_acy_z500]`:

```toml
z500_smooth_method   = "uniform"   # "uniform" or "gaussian"
z500_smooth_scale_km = 100         # window size (uniform) or sigma (gaussian) in km
```

Use `"gaussian"` for smoother anomaly fields; `"uniform"` (default) matches
the original algorithm exactly.

---

## Data download

Use `download_ERA5.py` to download all ERA5 input files from the
[Copernicus CDS](https://cds.climate.copernicus.eu/how-to-api).
Requires a CDS account and a valid `~/.cdsapirc` credentials file.

```bash
# Download all variables for a full year range
python download_ERA5.py --year-start 1979 --year-end 2024

# Download only Z500 and SLP for specific years
python download_ERA5.py --years 2020 2023 --datasets z500 slp

# Keep intermediate monthly files after concatenation
python download_ERA5.py --year-start 2020 --year-end 2024 --keep-monthly

# Write to a custom directory
python download_ERA5.py --year-start 2020 --year-end 2024 --outdir /scratch/era5
```

Available datasets: `z500`, `z200`, `z300`, `z850`, `slp`, `pr`

The script downloads data month-by-month (CDS best practice) and
concatenates the results into annual files matching the AtmoTrack naming
convention. Interrupted downloads can be resumed safely — already-downloaded
files are skipped.

---

## Expected input data layout

```
data_era5/
  era5_daily_500hPa_YYYY.nc   # geopotential (z) + temperature at 500 hPa
  era5_daily_200hPa_YYYY.nc   # u-wind at 200 hPa
  era5_daily_300hPa_YYYY.nc   # u-wind at 300 hPa
  era5_daily_850hPa_YYYY.nc   # t, u, v at 850 hPa
  era5_daily_PR_YYYY.nc       # total precipitation (hourly)
  era5_daily_SLP_YYYY.nc      # mean sea-level pressure (msl, Pa)
```

All files use `valid_time` as the time dimension and `latitude`/`longitude`
as coordinate variables (standard ERA5 CDS download format).

---

## Entry scripts

| Script | What it does |
|---|---|
| `COL_tracking_ERA5.py` | Detect and track Cut-Off Lows from 500 hPa Z anomalies |
| `CY_ACY500_tracking_ERA5.py` | Track upper-level cyclones/anticyclones from 500 hPa Z |
| `SLP_tracking_ERA5.py` | Track surface cyclones/anticyclones from SLP |
| `calc_COL_stats_all_watersheds.py` | Aggregate per-COL precipitation stats by watershed |
| `plot_COL_stats.py` | Plot annual statistics and trends |

Run any script from the project root directory, e.g.:

```bash
python COL_tracking_ERA5.py
```

All tracking scripts use `n_jobs=-1` (all available CPU cores via
`joblib.Parallel`). To limit parallelism, set `n_jobs=N` inside the
script's `main()` function.

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
algorithms. Import individual functions from it rather than running it
directly.

`utils.py` provides the shared logger and TTY-aware ANSI colour helpers used
by all scripts.

`constants.py` holds physical constants (gravity, Earth radius, etc.).

---

## Watershed mask

The watershed mask (`watershed_mask_medsea.nc`) is a pre-built NetCDF that
maps each grid cell to a watershed or ocean region ID. It is read by
`calc_COL_stats_all_watersheds.py` at runtime.

To rebuild the mask from scratch (e.g. for a different domain or shapefile),
use the utilities in `Misc/`:

```bash
# 1. Build the combined watershed + Mediterranean sea mask
python Misc/select_multipleregion_ERA5_shapefile.py \
    --input            data_era5/era5_daily_PR_202410.nc \
    --lsm              /path/to/era5_sst_lsm.nc \
    --watershed-shp    /path/to/watersheds.shp \
    --ocean-shp        /path/to/ocean_regions.shp \
    --output           watershed_mask_medsea.nc

# 2. (Optional) create a single-region binary mask
python Misc/select_region_ERA5_shapefile.py \
    --input     data_era5/era5_daily_PR_202410.nc \
    --shapefile /path/to/region.shp \
    --output    mask_region.nc
```

### Watershed codes

| Code | Watershed |
|---|---|
| 22 | CAT (Catalonia) |
| 23 | EBR (Ebro) |
| 16 | JUC (Júcar) |
| 8  | BAL (Balearic) |
| 7  | SEG (Segura) |
| 21 | SUR (Sur) |
| 25 | MED (Mediterranean open sea) |

---

## Misc utilities

| Script | What it does |
|---|---|
| `Misc/select_multipleregion_ERA5_shapefile.py` | Build the watershed + ocean mask NetCDF |
| `Misc/select_region_ERA5_shapefile.py` | Create a binary mask for a single shapefile region |
| `Misc/plot_shapefile.py` | Visualise a single-region shapefile on a map |
| `Misc/plot_shapefile_multiple_regions.py` | Visualise all regions in a shapefile |

All accept `--help` for full usage information.

---

## Linting

[ruff](https://docs.astral.sh/ruff/) is configured in `pyproject.toml`:

```bash
ruff check .        # check for issues
ruff format .       # auto-format
ruff check --fix .  # auto-fix safe issues
```

---

## Smoke test

A self-contained smoke test verifies the full pipeline on 7 days of
synthetic ERA5-like data (no real files required):

```bash
pytest test_smoke.py -v

# or without pytest:
python test_smoke.py
```

The test covers imports, Z500 tracking, SLP tracking, and COL tracking,
and checks that output arrays have the correct shape and contain
non-negative object IDs.
