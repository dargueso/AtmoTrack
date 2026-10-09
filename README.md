# AtmoTrack

[![Smoke test](https://github.com/dargueso/AtmoTrack/actions/workflows/test.yml/badge.svg)](https://github.com/dargueso/AtmoTrack/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
<!-- Once Zenodo has minted the DOI, add:
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.XXXXXXX.svg)](https://doi.org/10.5281/zenodo.XXXXXXX)
-->

Atmospheric system tracking from any CF-compliant NetCDF dataset.

AtmoTrack detects and tracks **Cut-Off Lows (COL)**, **upper-level and surface
cyclones/anticyclones**, **tropical cyclones (TC)**, **jet streams**,
**atmospheric rivers (AR)**, **Mesoscale Convective Systems (MCS)** and
**fronts**. It works with ERA5 reanalysis out of the box and with WRF, CESM or
any other source by editing a single `config.toml` — no Python changes needed.

Each tracker is available both as a command-line tool (`atmotrack-col`, …) and
as a Python function (`from atmotrack.tracking import COL_tracking`).

---

## Citing AtmoTrack

If you use AtmoTrack in published research, please cite it:

> Argüeso, D. (2026). *AtmoTrack: Atmospheric system tracking from CF-compliant
> NetCDF data* (v1.0.0). https://github.com/dargueso/AtmoTrack

A machine-readable citation is in `CITATION.cff` (GitHub's **"Cite this
repository"** button exports BibTeX and APA). Releases are archived on Zenodo;
the DOI badge above is updated with each release.

---

## Installation

Python ≥ 3.11 is required.

```bash
git clone https://github.com/dargueso/AtmoTrack.git
cd AtmoTrack

# conda (recommended) — creates the "atmotrack" environment and installs the package
conda env create -f environment.yml
conda activate atmotrack

# or pip, into an existing environment
pip install -e .                      # core library + commands
pip install -e ".[tc]"                # + cartopy/shapely for the TC land/sea test
pip install -e ".[download]"          # + cdsapi for atmotrack-download-era5
pip install -e ".[dev,tc,download]"   # everything, including pytest and ruff
```

The install registers the `atmotrack-*` commands listed below.

---

## Configuration

All thresholds, the domain, the paths and the input variable names live in one
TOML file. AtmoTrack looks for it in this order:

1. the file named by the `ATMOTRACK_CONFIG` environment variable;
2. `config.toml` in the current working directory;
3. the default shipped inside the package.

Start a project by copying the default and editing it:

```bash
cd /path/to/my_project
atmotrack-config --init        # writes ./config.toml
atmotrack-config               # shows which file the commands will use
atmotrack-config --show        # prints its contents
```

Library users can switch configuration at run time with
`atmotrack.config.load("/path/to/config.toml")`.

### `[paths]`

| Key | Description |
|---|---|
| `data_input` | Directory with input NetCDF files for tracking (any source) |
| `data_era5` | Directory where `atmotrack-download-era5` writes (defaults to `data_input`) |
| `data_tracking` | Output directory for tracking NetCDF files |
| `path_in` | Output prefix used by `MCS_tracking` |

Relative paths are resolved from the directory where a command is launched.

### `[data_source]`

Controls how input files are found and read. The values below are the ERA5
defaults.

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

### Tracker sections

Each tracker has its own section: `[general]` (`DT`, the data time step in
hours), `[cy_acy_z500]`, `[cy_acy_slp]`, `[col]`, `[fronts]`, `[jetstream]`,
`[atmospheric_rivers]`, `[tropical_cyclones]` and `[mcs]`. Every key is
commented in the default file (`atmotrack-config --show`). For example:

```toml
[cy_acy_z500]
z500_smooth_method   = "uniform"   # "uniform" or "gaussian"
z500_smooth_scale_km = 100         # window size (uniform) or sigma (gaussian) in km
```

---

## Data download (ERA5 only)

`atmotrack-download-era5` fetches the ERA5 input files from the
[Copernicus CDS](https://cds.climate.copernicus.eu/how-to-api). It needs the
`download` extra, a CDS account and a valid `~/.cdsapirc`.

```bash
atmotrack-download-era5 --year-start 1979 --year-end 2024   # full year range
atmotrack-download-era5 --current-year                       # current (incomplete) year
atmotrack-download-era5 --years 2020 2023 --datasets z500 slp
atmotrack-download-era5 --year-start 2020 --year-end 2024 --keep-monthly
```

Available datasets: `z500`, `z200`, `z300`, `z850`, `slp`, `pr`.

### Expected input layout (ERA5 defaults)

```
data_input/
  era5_daily_500hPa_*.nc   # geopotential (z) + u-wind at 500 hPa
  era5_daily_200hPa_*.nc   # u, v wind at 200 hPa
  era5_daily_850hPa_*.nc   # t, u, v, q at 850 hPa
  era5_daily_SLP_*.nc      # mean sea-level pressure (msl, Pa)
  era5_daily_PR_*.nc       # total precipitation (tp, m; hourly)
  era5_daily_IVT_*.nc      # integrated vapour transport (ivte, ivtn)
```

Files do not need to be organised one-per-year; any glob-matching set of files
is merged automatically.

---

## Commands

| Command | What it does | Output file |
|---|---|---|
| `atmotrack-col` | Detect and track Cut-Off Lows from 500 hPa Z anomalies | `col_z500_{year}.nc` |
| `atmotrack-cy-z500` | Track upper-level cyclones/anticyclones from 500 hPa Z | `cy_z500_{year}.nc` |
| `atmotrack-cy-slp` | Track surface cyclones/anticyclones from SLP | `cy_slp_{year}.nc` |
| `atmotrack-tc` | Filter tropical cyclones from SLP cyclone objects (run `atmotrack-cy-slp` first) | `tc_{year}.nc` |
| `atmotrack-jet` | Track jet stream objects from 200 hPa wind speed anomalies | `jet_{year}.nc` |
| `atmotrack-ar-850` | Track atmospheric rivers from 850 hPa moisture flux | `ar850_{year}.nc` |
| `atmotrack-ar-ivt` | Track atmospheric rivers from integrated vapour transport | `ar_ivt_{year}.nc` |
| `atmotrack-download-era5` | Download ERA5 input files from the CDS | `era5_daily_*_{year}.nc` |
| `atmotrack-config` | Show or initialise the configuration file | `config.toml` |

Output files are written to `data_tracking`, one per year. Each run also writes
`out.log` in the current directory.

Common flags (all tracking commands):

| Flag | Description |
|---|---|
| `--year-start` / `--year-end` | Year range to process (default 1940–2024, clipped to the data) |
| `--current-year` | Process only the current calendar year |
| `--jobs` / `-j` | Number of parallel workers, one year each (default: 8) |
| `--verbose` / `-v` | DEBUG-level logging |

Example:

```bash
atmotrack-col --year-start 2000 --year-end 2024 --jobs 8 -v
```

---

## Library

```python
from atmotrack import config as cfg
from atmotrack.io import open_pattern, slice_year, load_grid, load_times
from atmotrack.tracking import CY_ACY_z500_tracking, COL_tracking

ds = slice_year(open_pattern("pattern_z500"), 2024)
lon2d, lat2d = load_grid(ds)
times = load_times(ds)
cy, acy = CY_ACY_z500_tracking(ds[cfg.var_z500].values, times, lon2d, lat2d, nc_file=None)
```

| Module | Contents |
|---|---|
| `atmotrack.config` | Configuration loader; every TOML key is a module attribute (`cfg.DT`, `cfg.col_min_dur`, …); `load(path)` |
| `atmotrack.io` | Source-agnostic loaders: `open_pattern`, `available_years`, `slice_year`, `load_grid`, `load_times`, `infer_dt` |
| `atmotrack.tracking.shared` | Grid helpers and object utilities (`haversine`, `calc_grid_distance_area`, `ConnectLon`, `BreakupObjects`, `clean_up_objects`, …) |
| `atmotrack.tracking.cy_z500` | `CY_ACY_z500_tracking` — 500 hPa cyclone/anticyclone tracking |
| `atmotrack.tracking.cy_slp` | `watershed_2d_overlap`, `CY_ACY_slp_tracking` — surface cyclone/anticyclone tracking |
| `atmotrack.tracking.col` | `Front_tracking`, `COL_tracking` — fronts and cut-off lows |
| `atmotrack.tracking.mcs` | `MCS_tracking` — mesoscale convective systems |
| `atmotrack.tracking.tc` | `TC_tracking` — tropical cyclone filtering |
| `atmotrack.tracking.jet` | `jetstream_tracking` — jet stream objects |
| `atmotrack.tracking.ar` | `AR_850hPa_tracking`, `AR_IVT_tracking` — atmospheric rivers |
| `atmotrack.constants` | Physical constants (`const.g`, `const.earth_radius`, …) |
| `atmotrack.utils` | Shared logger (`get_logger`) and TTY-aware ANSI colours |

The tracking functions are also re-exported from the top-level package
(`from atmotrack import COL_tracking`).

---

## Development

```bash
pip install -e ".[dev,tc]"
ruff check .            # lint
ruff format .           # format
pytest                  # smoke tests on synthetic data (no real files needed)
```

The smoke tests (`tests/test_smoke.py`) cover imports and every tracker:
Z500, SLP, COL, fronts, MCS, TC, jet stream and both AR methods. They run on
synthetic fields and take a few seconds.

The GitHub Actions workflow runs ruff and the tests on Python 3.11–3.13. A
pre-commit hook that runs ruff on the staged files is included; enable it once
per clone with:

```bash
git config core.hooksPath .githooks
```

See `CHANGELOG.md` for release notes.

---

## License

MIT — see `LICENSE`.
