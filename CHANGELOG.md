# Changelog

All notable changes to AtmoTrack are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-10-09

Generic input, fast per-year loading, and fixes to the lifetime and time-step
handling. **Tracking results change** with respect to 1.0.0 (see *Changed*); re-run
the trackers after upgrading.

### Added
- `atmotrack-check-input`: reports, per input stream, files, time axis (calendar,
  step, gaps, years), grid, variables and units, with a `ready` / `prepare` /
  `unsupported` verdict (`--json` available).
- `atmotrack-prepare`: converts any CF-compliant collection into canonical per-year
  files (`atmotrack_<stream>_<year>.nc`: ERA5 variable names and units, all streams
  at `DT`, chosen longitude convention, size-1 level dims squeezed, calendar kept);
  `--write-config` writes the matching configuration; `[prepare]` config section.
- File index: the time range of every input file is read once and cached
  (`.atmotrack_index.json`), and only the files containing the requested year are
  opened. Loading one year of ERA5 input went from ~43 min to seconds.
- Support for any latitude order, 0..360 or −180..180 longitudes, global periodic
  grids in either convention, curvilinear 2-D grids, and non-standard calendars
  (`noleap`, `360_day`, …) end to end, including the output files.
- `atmotrack.grid`, `atmotrack.timeaxis`, `atmotrack.output`, `atmotrack.inputcheck`,
  `atmotrack.prepare` modules; `atmotrack.config.get()` with rename hints.
- Tests for lifetime filtering, grid conventions, cftime, the file index and the
  checker/preparer pipeline.

### Changed
- **Minimum lifetime** in `BreakupObjects` and `clean_up_objects` was divided by the
  time step twice, so objects only needed `MinTime/DT²` steps. It now counts steps
  once. Fewer short-lived cyclones, anticyclones, jets and ARs survive.
- **Time step**: `[general] DT` is the single tracking step (default now 6 h; it was
  3 h while the ERA5 pressure-level data are 6-hourly, which halved every hour-based
  window for z500/COL/jet/AR). Every stream is aligned to `DT` on load: instantaneous
  fields subsampled, precipitation accumulated. A step that does not divide `DT` is an
  error instead of a warning.
- `atmotrack-tc` now reads t850 and SLP on the same `DT` axis (it mixed 6-hourly t850
  with 3-hourly SLP objects before). `TC_min_duration` (steps) became
  `TC_min_duration_h` (hours) and the hard-coded 8-step persistence became
  `TC_persistence_h`; `watershed_time_window_h` replaces the hard-coded 4-step window.
- Precipitation is aligned to the z500 time axis by time stamp, not by count.
- The COL "poleward eastward flow" test uses the latitude direction of the grid
  instead of assuming ERA5's north-to-south order; longitude bounds are compared in
  −180..180 whatever the input convention.
- Uniform smoothing windows are forced to odd sizes so results do not depend on the
  grid orientation.
- `Front_tracking` evaluates the temperature gradient per 100 km instead of per grid
  cell.
- Output files carry 2-D `lat`/`lon` (plus 1-D coordinates on regular grids), the
  input calendar, int32 labels, float32 fields and compression on every variable.
- `atmotrack-download-era5` also fetches `v` at 200 hPa and `q` at 850 hPa.
- README: WRF example corrected (`PSFC` is not sea-level pressure); input
  requirements, checking and preparing documented.

### Fixed
- Dateline detection no longer requires −180..180 longitudes.
- `is_land()` receives −180..180 longitudes.

## [1.0.0] — 2026-10-09

First public release. DOI: [10.5281/zenodo.23263418](https://doi.org/10.5281/zenodo.23263418)

### Added
- Installable `atmotrack` package (`pip install -e .`) with a `src/` layout.
- Console commands for every tracker: `atmotrack-col`, `atmotrack-cy-z500`,
  `atmotrack-cy-slp`, `atmotrack-tc`, `atmotrack-jet`, `atmotrack-ar-850`,
  `atmotrack-ar-ivt`, plus `atmotrack-download-era5` (Copernicus CDS) and
  `atmotrack-config` (show or initialise the configuration file).
- Configuration lookup order: `$ATMOTRACK_CONFIG`, then `./config.toml`, then
  the default shipped inside the package. `atmotrack.config.load(path)` switches
  configuration at run time.
- `tc` and `download` optional dependency groups.
- `CITATION.cff`, `.zenodo.json` and this changelog.

### Changed
- Library modules live under `atmotrack.*` (`atmotrack.config`, `atmotrack.io`,
  `atmotrack.tracking`, `atmotrack.constants`, `atmotrack.utils`). The flat
  top-level modules (`atmotrack_config`, `atmotrack_io`, `tracking_functions`,
  `constants`, `utils`) and the `*_tracking_ERA5.py` scripts were removed.
- Project-specific material (watershed statistics, event plotting, the expert
  evaluation site, precipitable-water download) moved to a separate repository.
  The `watershed_mask`, `hires_pr_pattern`, `stats_dir` and `plots_dir` config
  keys were removed.

### Trackers (unchanged algorithms)
- Cut-Off Lows from 500 hPa geopotential anomalies (with fronts diagnostic).
- Upper-level (500 hPa) and surface (SLP) cyclones/anticyclones.
- Tropical cyclone filter on surface cyclone objects.
- Jet streams from 200 hPa wind-speed anomalies.
- Atmospheric rivers from 850 hPa moisture flux or integrated vapour transport.
- Mesoscale convective systems from precipitation and brightness temperature.

[1.1.0]: https://github.com/dargueso/AtmoTrack/releases/tag/v1.1.0
[1.0.0]: https://github.com/dargueso/AtmoTrack/releases/tag/v1.0.0
