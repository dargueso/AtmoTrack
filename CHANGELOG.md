# Changelog

All notable changes to AtmoTrack are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

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

[1.0.0]: https://github.com/dargueso/AtmoTrack/releases/tag/v1.0.0
