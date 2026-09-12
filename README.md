# AtmoTrack

Atmospheric system tracking from any CF-compliant NetCDF dataset.

Tracks Cut-Off Lows (COL), upper-level and surface cyclones/anticyclones,
Mesoscale Convective Systems (MCS), fronts, tropical cyclones (TC),
jet streams, and atmospheric rivers (AR).

Designed for ERA5 reanalysis out of the box; supports WRF (Lambert Conformal),
CESM, and any other source by editing `config.toml` — no Python changes needed.

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

All thresholds, domain settings, file paths, and variable names are set in
**`config.toml`**. Edit this file before running any script — no source code
changes needed.

### `[paths]`

| Key | Description |
|---|---|
| `data_input` | Directory with input NetCDF files for tracking (any source) |
| `data_era5` | Directory where `download_ERA5.py` writes its output (defaults to `data_input`) |
| `data_tracking` | Output directory for tracking NetCDF files |
| `watershed_mask` | Path to `watershed_mask_medsea.nc` |
| `hires_pr_pattern` | Glob pattern for ERA5-Land precipitation files |
| `stats_dir` | Output directory for per-event statistics CSVs |

### `[data_source]`

Controls how input files are found and read. All values below are the ERA5
defaults — existing ERA5 users need not change anything.

```toml
[data_source]
# Coordinate names
lat_var   = "latitude"    # 1-D → meshgridded; 2-D → used directly
lon_var   = "longitude"
lat_is_2d = false         # true for WRF XLAT/XLONG
time_var  = "valid_time"  # CF-compliant time dimension name

# Glob patterns (relative to data_input); multiple files are merged automatically
pattern_z500 = "era5_daily_500hPa_*.nc"
pattern_z200 = "era5_daily_200hPa_*.nc"
pattern_z850 = "era5_daily_850hPa_*.nc"
pattern_slp  = "era5_daily_SLP_*.nc"
pattern_pr   = "era5_daily_PR_*.nc"
pattern_ivt  = "era5_daily_IVT_*.nc"

# Variable names within each file
var_z500 = "z"     var_u200 = "u"     var_v200 = "v"
var_u850 = "u"     var_v850 = "v"     var_t850 = "t"
var_msl  = "msl"   var_pr   = "tp"    var_q850 = "q"
var_ivte = "ivte"  var_ivtn = "ivtn"
```

### Using WRF data (example)

```toml
[paths]
data_input = "/scratch/wrf_output"

[data_source]
lat_var   = "XLAT"
lon_var   = "XLONG"
lat_is_2d = true
time_var  = "time"
pattern_slp  = "wrfout_d01_*.nc"
var_msl  = "PSFC"
```

### Z500 smoothing method

```toml
[cy_acy_z500]
z500_smooth_method   = "uniform"   # "uniform" or "gaussian"
z500_smooth_scale_km = 100         # window size (uniform) or sigma (gaussian) in km
```

---

## Data download (ERA5 only)

Use `download_ERA5.py` to download ERA5 input files from the
[Copernicus CDS](https://cds.climate.copernicus.eu/how-to-api).
Requires a CDS account and a valid `~/.cdsapirc` credentials file.

```bash
# Download all variables for a full year range
python download_ERA5.py --year-start 1979 --year-end 2024

# Download only the current (possibly incomplete) year
python download_ERA5.py --current-year

# Download specific years and datasets
python download_ERA5.py --years 2020 2023 --datasets z500 slp

# Keep intermediate monthly files after concatenation
python download_ERA5.py --year-start 2020 --year-end 2024 --keep-monthly
```

Available datasets: `z500`, `z200`, `z300`, `z850`, `slp`, `pr`

---

## Expected input data layout (ERA5 defaults)

```
data_era5/
  era5_daily_500hPa_*.nc   # geopotential (z) + u-wind at 500 hPa
  era5_daily_200hPa_*.nc   # u, v wind at 200 hPa
  era5_daily_850hPa_*.nc   # t, u, v, q at 850 hPa
  era5_daily_SLP_*.nc      # mean sea-level pressure (msl, Pa)
  era5_daily_PR_*.nc       # total precipitation (tp, m; hourly)
  era5_daily_IVT_*.nc      # integrated vapour transport (ivte, ivtn)
```

Files do not need to be organised one-per-year; any glob-matching set of files
is merged automatically. For ERA5, `download_ERA5.py` produces annual files
that match the default patterns.

---

## Entry scripts

| Script | What it does |
|---|---|
| `COL_tracking_ERA5.py` | Detect and track Cut-Off Lows from 500 hPa Z anomalies |
| `CY_ACY500_tracking_ERA5.py` | Track upper-level cyclones/anticyclones from 500 hPa Z |
| `SLP_tracking_ERA5.py` | Track surface cyclones/anticyclones from SLP |
| `TC_tracking_ERA5.py` | Filter tropical cyclones from SLP cyclone objects (run SLP first) |
| `JetStream_tracking_ERA5.py` | Track jet stream objects from 200 hPa wind speed anomalies |
| `AR_850hPa_tracking_ERA5.py` | Track atmospheric rivers from 850 hPa moisture flux |
| `AR_IVT_tracking_ERA5.py` | Track atmospheric rivers from integrated vapour transport |
| `calc_COL_stats_all_watersheds.py` | Aggregate per-COL precipitation stats by watershed |
| `plot_COL_stats.py` | Plot annual statistics and trends |

Run any script from the project root, e.g.:

```bash
python COL_tracking_ERA5.py --year-start 2000 --year-end 2024 --jobs 8 -v
```

Common flags (all tracking scripts):

| Flag | Description |
|---|---|
| `--year-start` / `--year-end` | Year range to process |
| `--jobs` / `-j` | Number of parallel workers (default: 8) |
| `--verbose` / `-v` | Enable DEBUG-level logging |

---

## Output

Tracking output NetCDF files are written to `data_tracking/`, one per year:

| File | Content |
|---|---|
| `col_z500_{year}.nc` | COL object labels + supporting fields |
| `cy_z500_{year}.nc` | 500 hPa cyclone/anticyclone labels |
| `cy_slp_{year}.nc` | Surface cyclone/anticyclone labels |
| `tc_{year}.nc` | Tropical cyclone labels |
| `jet_{year}.nc` | Jet stream object labels |
| `ar850_{year}.nc` | 850 hPa AR object labels |
| `ar_ivt_{year}.nc` | IVT-based AR object labels |

Statistics CSVs are written to `stats_dir/`; plots to `plots_dir/`.

---

## Core library

The `tracking/` package contains all detection, labeling, and tracking
algorithms, split by concern:

| Module | Contents |
|---|---|
| `tracking/shared.py` | Grid helpers, object utilities (`haversine`, `calc_grid_distance_area`, `ConnectLon`, `ConnectLon_on_timestep`, `BreakupObjects`, `clean_up_objects`, …) |
| `tracking/cy_z500.py` | `CY_ACY_z500_tracking` — 500 hPa cyclone/anticyclone tracking |
| `tracking/cy_slp.py` | `watershed_2d_overlap`, `CY_ACY_slp_tracking` — surface cyclone/anticyclone tracking |
| `tracking/col.py` | `Front_tracking`, `COL_tracking` — fronts and cut-off lows |
| `tracking/mcs.py` | `MCS_tracking` — mesoscale convective systems |
| `tracking/tc.py` | `TC_tracking` — tropical cyclone filtering |
| `tracking/jet.py` | `jetstream_tracking` — jet stream objects |
| `tracking/ar.py` | `AR_850hPa_tracking`, `AR_IVT_tracking` — atmospheric rivers |

`atmotrack_io.py` provides the source-agnostic file loader used by all entry scripts.

Import directly from the package or from the backward-compatible shim:

```python
from tracking import COL_tracking, CY_ACY_z500_tracking  # preferred
from tracking_functions import COL_tracking  # also works
```

`utils.py` provides the shared logger and TTY-aware ANSI colour helpers.

`constants.py` holds physical constants (gravity, Earth radius, etc.).

---

## Watershed mask

The watershed mask (`watershed_mask_medsea.nc`) maps each grid cell to a
watershed or ocean region ID. It is read by `calc_COL_stats_all_watersheds.py`
at runtime.

To rebuild the mask for a different domain, use the utilities in `Misc/`:

```bash
python Misc/select_multipleregion_ERA5_shapefile.py \
    --input            data_era5/era5_daily_PR_202410.nc \
    --lsm              /path/to/era5_sst_lsm.nc \
    --watershed-shp    /path/to/watersheds.shp \
    --ocean-shp        /path/to/ocean_regions.shp \
    --output           watershed_mask_medsea.nc
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

A self-contained smoke test verifies the full pipeline on synthetic data
(no real files required):

```bash
pytest test_smoke.py -v

# or without pytest:
python test_smoke.py
```

Covers 10 tests: imports, Z500 tracking, SLP tracking, COL tracking,
front tracking, MCS tracking, TC tracking, jet stream tracking,
850 hPa AR tracking, and IVT AR tracking.
