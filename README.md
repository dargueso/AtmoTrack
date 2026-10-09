# AtmoTrack

[![Smoke test](https://github.com/dargueso/AtmoTrack/actions/workflows/test.yml/badge.svg)](https://github.com/dargueso/AtmoTrack/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23263417.svg)](https://doi.org/10.5281/zenodo.23263417)

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
> NetCDF data* (v1.1.0). Zenodo. https://doi.org/10.5281/zenodo.23263417

A machine-readable citation is in `CITATION.cff` (GitHub's **"Cite this
repository"** button exports BibTeX and APA). Releases are archived on Zenodo.
The badge above points to the concept DOI (10.5281/zenodo.23263417), which always resolves
to the latest version; cite the version DOI of a specific release from `CHANGELOG.md`.

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
| `[prepare] output_dir` | Where `atmotrack-prepare` writes canonical files |

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

### Time step (`[general] DT`)

All trackers run at one time step, `DT` (hours, default 6). Every input stream is
brought to that step when it is loaded: instantaneous fields at a finer step are
subsampled (3-hourly SLP → 6-hourly), precipitation is accumulated over each `DT`
window. A stream whose step does not divide `DT` is refused; run
`atmotrack-check-input` to see what applies to your files. All lifetime and window
thresholds are in hours and converted with `DT` internally.

### Input requirements (any dataset)

AtmoTrack reads any CF-compliant NetCDF collection as long as:

- all streams are on the **same horizontal grid** (regular lat/lon in any order and
  any longitude convention, or a curvilinear 2-D grid such as WRF Lambert);
- fields are on the **required pressure levels** already (500 hPa `z`, 200 hPa `u`/`v`,
  850 hPa `u`/`v`/`t`/`q`, surface `msl`, `tp`); vertical interpolation is not done here;
- winds are **earth-relative** (no rotation or destaggering is applied);
- the time axis is regular; any CF calendar (`standard`, `noleap`, `360_day`, …) works.

Units are the ERA5/SI ones: `z` in m² s⁻², `msl` in Pa, `tp` in m per input step,
`t` in K, `q` in kg kg⁻¹, winds in m s⁻¹, IVT in kg m⁻¹ s⁻¹. Other units
(geopotential height in m, hPa, °C, g kg⁻¹, precipitation rates in kg m⁻² s⁻¹ or
mm h⁻¹) are converted by `atmotrack-prepare`.

### Checking the input

```bash
atmotrack-check-input          # text report, exit code 0 when ready
atmotrack-check-input --json   # machine-readable
```

For every configured stream it reports the files found, the time axis (calendar,
step, gaps, years), the grid, each variable with its units, and a verdict:
`ready` (the trackers read it directly), `prepare` (convertible with
`atmotrack-prepare`, e.g. unit conversion, 0..360 longitudes, a size-1 level
dimension, time-step alignment) or `unsupported` (with the reason, e.g. streams on
different grids, unknown units, staggered winds).

### Preparing the input

```bash
atmotrack-prepare --year-start 1990 --year-end 2020 --jobs 8 --write-config config.prepared.toml
```

`atmotrack-prepare` writes canonical per-year files `atmotrack_<stream>_<year>.nc`
to `[prepare] output_dir`: ERA5 variable names and units, all streams at `DT`,
longitudes in `[prepare] lon_convention`, size-1 level dimensions squeezed, the
calendar kept, 2-D `lat`/`lon` for curvilinear grids. It then prints the
`[data_source]` block that reads them (`--write-config` writes a complete config).
Source units are taken from the `units` attributes; override them with
`units_<var>` keys in `[prepare]` when they are missing or wrong.

### Using WRF data (example)

```toml
[paths]
data_input = "/scratch/wrf_postprocessed"

[data_source]
lat_var   = "XLAT"
lon_var   = "XLONG"
lat_is_2d = true
time_var  = "time"
pattern_slp  = "wrf_slp_*.nc"    # sea-level pressure diagnosed from the model output
var_msl  = "slp"
pattern_z500 = "wrf_plev_*.nc"   # fields interpolated to pressure levels
var_z500 = "z500"
```

The surface pressure `PSFC` is **not** a substitute for sea-level pressure; WRF
output needs a pressure-level interpolation and SLP diagnostic first (e.g. with
wrf-python or xwrf), with winds rotated to earth-relative. Then run
`atmotrack-check-input`.

### Tracker sections

Each tracker has its own section: `[general]` (`DT`, the tracking time step in
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
| `atmotrack-check-input` | Report whether the configured input can be tracked (ready / prepare / unsupported) | report |
| `atmotrack-prepare` | Convert any input to canonical per-year files at `DT` | `atmotrack_{stream}_{year}.nc` |
| `atmotrack-config` | Show or initialise the configuration file; `--prepared` prints the block for prepared files | `config.toml` |

Output files are written to `data_tracking`, one per year, with dims
`(time, latitude, longitude)` plus 2-D `lat`/`lon` coordinates on regular grids and
`(time, y, x)` with 2-D `lat`/`lon` on curvilinear grids; the time coordinate keeps
the calendar of the input. Each run also writes `out.log` in the current directory.

The first run over a collection indexes the time range of every input file
(`.atmotrack_index.json` next to the data, or under `~/.cache/atmotrack`); later
runs open only the files that contain the requested year.

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
| `atmotrack.io` | Source-agnostic loaders: `open_years` (indexed per-year access), `open_pattern`, `available_years`, `load_grid`, `load_times`, `subsample_to_dt`, `accumulate_to_dt` |
| `atmotrack.grid` | `Grid` (regular/curvilinear, lat order, lon convention, periodicity, spacing, cell area), `to_pm180` |
| `atmotrack.timeaxis` | Calendar-aware time helpers (`load_times`, `step_hours`, `encode_times`) |
| `atmotrack.inputcheck` / `atmotrack.prepare` | The input checker and the canonical-file writer behind the two commands |
| `atmotrack.output` | `write_tracking_file`, the common NetCDF writer |
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

The tests run on synthetic fields in a few seconds: smoke tests of every tracker
(`test_smoke.py`), lifetime filtering (`test_lifetime.py`), grid conventions and a
curvilinear grid (`test_grid.py`), cftime calendars (`test_timeaxis.py`), the file
index and time-step alignment (`test_io_index.py`), and the checker/preparer on a
non-ERA5 collection end to end (`test_prepare.py`).

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
