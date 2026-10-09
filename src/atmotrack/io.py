"""
atmotrack.io — Source-agnostic NetCDF loader for AtmoTrack.

Input files are found through glob patterns in ``config.toml`` (``[data_source]``)
and read with xarray.  The loader works with any CF-compliant collection:

* ERA5 style — 1-D ``latitude``/``longitude`` coordinates, ``valid_time``
  dimension, one file per year;
* WRF / projected style — 2-D ``XLAT``/``XLONG`` arrays, ``time`` dimension;
* any other layout: set ``lat_var``, ``lon_var``, ``lat_is_2d``, ``time_var``
  and the ``pattern_*`` / ``var_*`` keys.

Per-year access goes through a **file index**: the time range of every file is
read once (metadata only) and cached in ``<data_input>/.atmotrack_index.json``,
so :func:`open_years` opens only the files that overlap the requested year
instead of merging the whole collection.

:func:`align_to_dt` brings a stream to the configured time step ``DT``:
instantaneous fields are subsampled, accumulated fields (precipitation) summed.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import pathlib
import tempfile
from dataclasses import asdict, dataclass
from glob import glob

import numpy as np
import pandas as pd
import xarray as xr

from atmotrack import config as cfg
from atmotrack import timeaxis

logger = logging.getLogger("atmotrack")

INDEX_NAME = ".atmotrack_index.json"
INDEX_VERSION = 1
_DROP_VARS = ("expver", "number")

# ---------------------------------------------------------------------------
# Opening files
# ---------------------------------------------------------------------------


def _preprocess(ds: xr.Dataset) -> xr.Dataset:
    """Drop ERA5 bookkeeping coordinates that differ between files."""
    return ds.drop_vars([v for v in _DROP_VARS if v in ds.variables], errors="ignore")


def _open(files: list[str]) -> xr.Dataset:
    if not files:
        raise FileNotFoundError("No files to open")
    if len(files) == 1:
        return _preprocess(xr.open_dataset(files[0]))
    return xr.open_mfdataset(
        files,
        combine="nested",
        concat_dim=cfg.time_var,
        preprocess=_preprocess,
        compat="override",
        coords="minimal",
        data_vars="minimal",
    )


def pattern_files(pattern_key: str) -> list[str]:
    """Sorted files matching ``cfg.<pattern_key>`` under ``cfg.data_input``."""
    pattern = getattr(cfg, pattern_key)
    data_dir = pathlib.Path(cfg.data_input)
    return sorted(glob(str(data_dir / pattern)))


def open_pattern(pattern_key: str) -> xr.Dataset:
    """Open **all** files matching ``cfg.<pattern_key>`` as one dataset.

    Prefer :func:`open_years` for per-year work: it opens only the files that
    contain the requested year.
    """
    files = pattern_files(pattern_key)
    if not files:
        raise FileNotFoundError(
            f"No files found matching pattern '{getattr(cfg, pattern_key)}' in "
            f"'{cfg.data_input}'. Check data_input and {pattern_key} in config.toml."
        )
    return _open(files)


# ---------------------------------------------------------------------------
# File index
# ---------------------------------------------------------------------------


@dataclass
class FileEntry:
    path: str
    mtime: float
    size: int
    t0: str  # ISO 8601, first time stamp
    t1: str  # ISO 8601, last time stamp
    n: int
    step_h: float
    calendar: str

    def overlaps(self, start_iso: str, end_iso: str) -> bool:
        return self.t0 <= end_iso and self.t1 >= start_iso


def _iso(t) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def _read_time_meta(path: str) -> FileEntry:
    st = os.stat(path)
    with xr.open_dataset(path, decode_times=True) as ds:
        if cfg.time_var not in ds.variables:
            raise KeyError(
                f"{path}: time variable '{cfg.time_var}' not found "
                f"(variables: {list(ds.variables)}). Set time_var in config.toml."
            )
        times = timeaxis.load_times(ds, cfg.time_var)
    step = timeaxis.step_hours(times) if len(times) > 1 else float("nan")
    return FileEntry(
        path=path,
        mtime=st.st_mtime,
        size=st.st_size,
        t0=_iso(times[0]),
        t1=_iso(times[-1]),
        n=len(times),
        step_h=step,
        calendar=timeaxis.calendar_of(times),
    )


def _index_paths() -> list[pathlib.Path]:
    """Candidate cache locations: next to the data, then the user cache dir."""
    data_dir = pathlib.Path(cfg.data_input).resolve()
    digest = hashlib.sha1(str(data_dir).encode()).hexdigest()[:12]
    user_cache = (
        pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache"))
        / "atmotrack"
        / f"index-{digest}.json"
    )
    return [data_dir / INDEX_NAME, user_cache]


def _load_index() -> dict:
    for p in _index_paths():
        if p.is_file():
            try:
                with open(p) as f:
                    data = json.load(f)
                if data.get("version") == INDEX_VERSION:
                    return data.get("files", {})
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def _save_index(entries: dict) -> None:
    payload = {"version": INDEX_VERSION, "files": entries}
    for p in _index_paths():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".idx-", suffix=".json")
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
            os.replace(tmp, p)
            return
        except OSError:
            continue
    logger.debug("Could not write the file index cache (read-only locations)")


def index_pattern(pattern_key: str, reindex: bool = False) -> list[FileEntry]:
    """Time-range index of the files matching ``cfg.<pattern_key>`` (cached)."""
    files = pattern_files(pattern_key)
    if not files:
        raise FileNotFoundError(
            f"No files found matching pattern '{getattr(cfg, pattern_key)}' in "
            f"'{cfg.data_input}'. Check data_input and {pattern_key} in config.toml."
        )
    cache = {} if reindex else _load_index()
    entries: list[FileEntry] = []
    changed = False
    for path in files:
        st = os.stat(path)
        cached = cache.get(path)
        if cached and cached.get("mtime") == st.st_mtime and cached.get("size") == st.st_size:
            entries.append(FileEntry(**cached))
            continue
        entry = _read_time_meta(path)
        cache[path] = asdict(entry)
        entries.append(entry)
        changed = True
    if changed:
        _save_index(cache)
    return entries


def available_years(source) -> list[int]:
    """Sorted calendar years present in a pattern (by key) or in an open dataset."""
    if isinstance(source, xr.Dataset):
        return sorted({int(y) for y in source[cfg.time_var].dt.year.values})
    years: set[int] = set()
    for e in index_pattern(source):
        years.update(range(int(e.t0[:4]), int(e.t1[:4]) + 1))
    return sorted(years)


def _year_bounds(year: int, halo_h: float = 0.0) -> tuple[str, str]:
    """ISO bounds of *year* extended by *halo_h* hours (proleptic Gregorian arithmetic)."""
    start = _dt.datetime(year, 1, 1) - _dt.timedelta(hours=halo_h)
    end = _dt.datetime(year + 1, 1, 1) - _dt.timedelta(seconds=1) + _dt.timedelta(hours=halo_h)
    return start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S")


def files_for_year(pattern_key: str, year: int, halo_h: float = 0.0) -> list[str]:
    """Files of the pattern that overlap *year* (± *halo_h* hours)."""
    start, end = _year_bounds(year, halo_h)
    return [e.path for e in index_pattern(pattern_key) if e.overlaps(start, end)]


def open_years(pattern_key: str, year: int, halo_h: float = 0.0) -> xr.Dataset:
    """Open only the files that contain *year* and return that year (± halo).

    Length-1 dimensions (e.g. a single pressure level) are squeezed.
    """
    files = files_for_year(pattern_key, year, halo_h)
    if not files:
        years = available_years(pattern_key)
        raise FileNotFoundError(
            f"No file of pattern '{getattr(cfg, pattern_key)}' contains year {year}. "
            f"Available years: {years[0]}–{years[-1]}"
            if years
            else "No files indexed."
        )
    ds = _open(files)
    times = timeaxis.load_times(ds, cfg.time_var)
    yrs = timeaxis.years(times)
    if halo_h > 0:
        start, end = _year_bounds(year, halo_h)
        iso = np.array([_iso(t) for t in times])
        mask = (iso >= start) & (iso <= end)
    else:
        mask = yrs == year
    return ds.isel({cfg.time_var: np.where(mask)[0]}).squeeze(drop=True)


def slice_year(ds: xr.Dataset, year: int) -> xr.Dataset:
    """Extract one calendar year from *ds* and squeeze length-1 dimensions."""
    return ds.sel({cfg.time_var: ds[cfg.time_var].dt.year == year}).squeeze(drop=True)


# ---------------------------------------------------------------------------
# Grid, time and dimension handling
# ---------------------------------------------------------------------------


def load_grid(ds: xr.Dataset):
    """Return ``(lon2d, lat2d)`` numpy arrays from *ds*.

    1-D coordinates (``cfg.lat_is_2d = false``) are expanded with meshgrid;
    2-D arrays are used directly (a leading time dimension, as in WRF
    ``XLAT``, is dropped).
    """
    lat = ds[cfg.lat_var]
    lon = ds[cfg.lon_var]
    if getattr(cfg, "lat_is_2d", False) or lat.ndim == 2:
        if lat.ndim == 3:
            lat = lat.isel({lat.dims[0]: 0})
            lon = lon.isel({lon.dims[0]: 0})
        return np.asarray(lon.values, dtype=float), np.asarray(lat.values, dtype=float)
    return np.meshgrid(np.asarray(lon.values, dtype=float), np.asarray(lat.values, dtype=float))


def spatial_dims(ds: xr.Dataset) -> tuple[str, str]:
    """Names of the (y, x) dimensions of the data variables."""
    lat = ds[cfg.lat_var]
    if lat.ndim >= 2:
        return lat.dims[-2], lat.dims[-1]
    return cfg.lat_var, cfg.lon_var


def load_times(ds: xr.Dataset):
    """Time index of *ds* (DatetimeIndex or CFTimeIndex)."""
    return timeaxis.load_times(ds, cfg.time_var)


def infer_dt(times) -> float:
    """Dominant time step in hours (falls back to ``cfg.DT`` for a single stamp)."""
    if len(times) < 2:
        return float(cfg.DT)
    return timeaxis.step_hours(times)


def ensure_tyx(da: xr.DataArray, ydim: str, xdim: str) -> xr.DataArray:
    """Return *da* ordered as ``(time, y, x)``, squeezing any other length-1 dims."""
    extra = [d for d in da.dims if d not in (cfg.time_var, ydim, xdim)]
    for d in extra:
        if da.sizes[d] != 1:
            raise ValueError(
                f"Variable '{da.name}' has an extra dimension '{d}' of length {da.sizes[d]}; "
                "select a single level before tracking (atmotrack-prepare can squeeze size-1 dims)."
            )
        da = da.isel({d: 0}, drop=True)
    if cfg.time_var not in da.dims:
        da = da.expand_dims(cfg.time_var)
    return da.transpose(cfg.time_var, ydim, xdim)


# ---------------------------------------------------------------------------
# Time-step alignment
# ---------------------------------------------------------------------------


def _check_divisor(step: float, dt: float, what: str) -> int:
    ratio = dt / step
    if not np.isclose(ratio, round(ratio)) or ratio < 1:
        raise ValueError(
            f"{what}: data step {step:g} h is not a divisor of DT={dt:g} h. "
            "Run atmotrack-check-input / atmotrack-prepare, or change [general] DT."
        )
    return int(round(ratio))


def subsample_to_dt(da: xr.DataArray, dt_h: float) -> xr.DataArray:
    """Keep the time stamps of an instantaneous field that fall on the DT grid."""
    times = timeaxis.load_times(da.to_dataset(name="_v"), cfg.time_var)
    step = timeaxis.step_hours(times)
    if np.isnan(step) or np.isclose(step, dt_h):
        return da
    _check_divisor(step, dt_h, f"'{da.name}'")
    mask = timeaxis.on_step_mask(times, dt_h)
    return da.isel({cfg.time_var: np.where(mask)[0]})


def accumulate_to_dt(
    da: xr.DataArray, dt_h: float, kind: str = "accumulated"
) -> tuple[xr.DataArray, xr.DataArray]:
    """Aggregate a precipitation stream to DT.

    Returns ``(total, peak)``: the total over each DT window and the maximum
    per-input-step value inside it.  Windows start on the DT grid of the day and
    are labelled by their start, i.e. the value at time *t* covers ``[t, t + DT)``;
    a trailing partial window is dropped.  *kind* is ``"accumulated"`` (values are
    amounts per input step) or ``"rate"`` (values are mean rates over the input
    step; converted to amounts with the step length in seconds).
    """
    times = timeaxis.load_times(da.to_dataset(name="_v"), cfg.time_var)
    step = timeaxis.step_hours(times)
    if np.isnan(step):
        return da, da
    if kind == "rate":
        da = da * (step * 3600.0)
    if np.isclose(step, dt_h):
        return da, da
    ratio = _check_divisor(step, dt_h, f"'{da.name}'")
    timeaxis.step_hours(times, strict=True)  # a regular axis is required for the reshape

    on_grid = np.where(timeaxis.on_step_mask(times, dt_h))[0]
    if on_grid.size == 0:
        raise ValueError(f"'{da.name}': no time stamp falls on the {dt_h:g}-hourly grid")
    first = int(on_grid[0])
    n_win = (len(times) - first) // ratio
    if n_win == 0:
        raise ValueError(f"'{da.name}': fewer than one DT window of data")
    da = da.transpose(cfg.time_var, ...)
    values = np.asarray(da.isel({cfg.time_var: slice(first, first + n_win * ratio)}).values)
    shape = (n_win, ratio) + values.shape[1:]
    windows = values.reshape(shape)
    new_times = np.asarray(da[cfg.time_var].values)[first : first + n_win * ratio : ratio]
    coords = {k: v for k, v in da.coords.items() if cfg.time_var not in v.dims}
    coords[cfg.time_var] = new_times
    total = xr.DataArray(
        windows.sum(axis=1), dims=da.dims, coords=coords, name=da.name, attrs=da.attrs
    )
    peak = xr.DataArray(
        windows.max(axis=1), dims=da.dims, coords=coords, name=da.name, attrs=da.attrs
    )
    return total, peak


def align_to_times(da: xr.DataArray, times, fill_value=0.0) -> xr.DataArray:
    """Reindex *da* onto *times*, filling gaps with *fill_value*."""
    return da.reindex(
        {cfg.time_var: np.asarray(times.values if hasattr(times, "values") else times)},
        fill_value=fill_value,
    )


def as_datetime_index(times) -> pd.DatetimeIndex | xr.CFTimeIndex:
    """Normalise a sequence of time stamps to a pandas/CFTime index."""
    if isinstance(times, (pd.DatetimeIndex, xr.CFTimeIndex)):
        return times
    arr = np.asarray(times)
    if np.issubdtype(arr.dtype, np.datetime64):
        return pd.DatetimeIndex(arr)
    return xr.CFTimeIndex(arr)
