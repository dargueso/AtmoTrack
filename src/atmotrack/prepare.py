"""Convert any input collection into AtmoTrack's canonical per-year files.

The canonical layout is what the trackers assume without any further
conversion:

* one file per stream and year: ``atmotrack_<stream>_<year>.nc`` with the
  ERA5 variable names (``z``, ``u``, ``v``, ``t``, ``q``, ``msl``, ``tp``,
  ``ivte``, ``ivtn``) and SI units as ERA5 ships them (``z`` in m² s⁻², ``msl``
  in Pa, ``tp`` in mm accumulated over the time step, ``t`` in K, ``q`` in
  kg kg⁻¹, winds in m s⁻¹, IVT in kg m⁻¹ s⁻¹);
* every stream at the configured time step ``DT`` (instantaneous fields
  subsampled, precipitation accumulated);
* dims ``(time, latitude, longitude)`` with 1-D coordinates for regular
  lat/lon grids, ``(time, y, x)`` with 2-D ``lat``/``lon`` for curvilinear
  grids; longitudes in the requested convention; calendar kept.

:func:`prepare_year` does the work for one year; the ``atmotrack-prepare``
command loops over years.  :mod:`atmotrack.inputcheck` uses the same unit table
to report what would change.
"""

from __future__ import annotations

import logging
import pathlib
import re

import numpy as np
import xarray as xr

from atmotrack import config as cfg
from atmotrack import timeaxis
from atmotrack.constants import const
from atmotrack.grid import P360, PM180, Grid, is_regular, to_360, to_pm180
from atmotrack.io import (
    accumulate_to_dt,
    ensure_tyx,
    load_grid,
    open_years,
    spatial_dims,
    subsample_to_dt,
)

logger = logging.getLogger("atmotrack")

# ---------------------------------------------------------------------------
# Streams and variables
# ---------------------------------------------------------------------------

#: stream -> list of (canonical variable name, config key of the source name)
STREAMS: dict[str, list[tuple[str, str]]] = {
    "z500": [("z", "var_z500")],
    "z200": [("u", "var_u200"), ("v", "var_v200")],
    "z850": [("u", "var_u850"), ("v", "var_v850"), ("t", "var_t850"), ("q", "var_q850")],
    "slp": [("msl", "var_msl")],
    "pr": [("tp", "var_pr")],
    "ivt": [("ivte", "var_ivte"), ("ivtn", "var_ivtn")],
}

#: which stream is accumulated rather than instantaneous
ACCUMULATED_STREAMS = {"pr"}

CANONICAL_UNITS = {
    "z": "m**2 s**-2",
    "u": "m s**-1",
    "v": "m s**-1",
    "t": "K",
    "q": "kg kg**-1",
    "msl": "Pa",
    "tp": "mm",
    "ivte": "kg m**-1 s**-1",
    "ivtn": "kg m**-1 s**-1",
}

LONG_NAMES = {
    "z": "geopotential",
    "u": "eastward wind",
    "v": "northward wind",
    "t": "air temperature",
    "q": "specific humidity",
    "msl": "mean sea-level pressure",
    "tp": "precipitation accumulated over the time step",
    "ivte": "eastward integrated vapour transport",
    "ivtn": "northward integrated vapour transport",
}


def _norm_units(units: str) -> str:
    """Normalise a units string for table lookup."""
    u = units.strip().lower()
    u = u.replace("**", "^").replace(" ", "").replace("·", "").replace("*", "")
    u = u.replace("^-1", "-1").replace("^-2", "-2").replace("^2", "2")
    u = u.replace("degrees_celsius", "degc").replace("celsius", "degc").replace("°c", "degc")
    u = u.replace("kelvin", "k").replace("pascal", "pa").replace("hectopascal", "hpa")
    u = (
        u.replace("millibar", "mb")
        .replace("meters", "m")
        .replace("metres", "m")
        .replace("meter", "m")
    )
    u = u.replace("/", "")  # "m/s" -> "ms", "kg/kg" -> "kgkg"
    return u


# Each entry: normalised unit -> (factor, offset, "amount" | "rate" | None)
_CONVERSIONS: dict[str, dict[str, tuple[float, float, str | None]]] = {
    "z": {
        "m2s-2": (1.0, 0.0, None),
        "m2s2": (1.0, 0.0, None),
        "m": (const.g, 0.0, None),  # geopotential height -> geopotential
        "gpm": (const.g, 0.0, None),
        "dam": (10.0 * const.g, 0.0, None),
    },
    "msl": {
        "pa": (1.0, 0.0, None),
        "hpa": (100.0, 0.0, None),
        "mb": (100.0, 0.0, None),
        "mbar": (100.0, 0.0, None),
        "kpa": (1000.0, 0.0, None),
    },
    "t": {
        "k": (1.0, 0.0, None),
        "degc": (1.0, 273.15, None),
        "c": (1.0, 273.15, None),
    },
    "q": {
        "kgkg-1": (1.0, 0.0, None),
        "kgkg": (1.0, 0.0, None),
        "1": (1.0, 0.0, None),
        "gkg-1": (1e-3, 0.0, None),
        "gkg": (1e-3, 0.0, None),
    },
    "wind": {
        "ms-1": (1.0, 0.0, None),
        "ms": (1.0, 0.0, None),
        "kt": (0.514444, 0.0, None),
        "knots": (0.514444, 0.0, None),
        "kmh-1": (1 / 3.6, 0.0, None),
        "kmh": (1 / 3.6, 0.0, None),
    },
    "tp": {
        # amounts per input step
        "m": (1000.0, 0.0, "amount"),
        "mm": (1.0, 0.0, "amount"),
        "kgm-2": (1.0, 0.0, "amount"),
        "kgm2": (1.0, 0.0, "amount"),
        # rates (converted with the input step length)
        "kgm-2s-1": (1.0, 0.0, "rate"),  # 1 kg m-2 s-1 = 1 mm s-1
        "kgm2s-1": (1.0, 0.0, "rate"),
        "mms-1": (1.0, 0.0, "rate"),
        "mms": (1.0, 0.0, "rate"),
        "mmh-1": (1 / 3600.0, 0.0, "rate"),
        "mmh": (1 / 3600.0, 0.0, "rate"),
        "mmhr-1": (1 / 3600.0, 0.0, "rate"),
        "mmhr": (1 / 3600.0, 0.0, "rate"),
        "mmday-1": (1 / 86400.0, 0.0, "rate"),
        "mmd-1": (1 / 86400.0, 0.0, "rate"),
        "ms-1": (1000.0 / 1.0, 0.0, "rate"),  # m s-1 -> mm s-1
    },
    "ivt": {
        "kgm-1s-1": (1.0, 0.0, None),
        "kgm1s-1": (1.0, 0.0, None),
        "kgms-1": (1.0, 0.0, None),
    },
}
_TABLE_FOR = {
    "z": "z",
    "u": "wind",
    "v": "wind",
    "t": "t",
    "q": "q",
    "msl": "msl",
    "tp": "tp",
    "ivte": "ivt",
    "ivtn": "ivt",
}


class UnitError(ValueError):
    pass


def source_units(da: xr.DataArray, canon: str) -> str | None:
    """Units of *da*: the ``units_<canon>`` config override, else the attribute."""
    override = getattr(cfg, f"units_{canon}", None)
    if override:
        return str(override)
    units = da.attrs.get("units")
    return str(units) if units is not None else None


def lookup_conversion(canon: str, units: str | None) -> tuple[float, float, str | None]:
    """``(factor, offset, kind)`` to bring *units* to the canonical units of *canon*."""
    table = _CONVERSIONS[_TABLE_FOR[canon]]
    if units is None:
        raise UnitError(
            f"'{canon}': the file has no `units` attribute; set units_{canon} in [prepare]"
        )
    key = _norm_units(units)
    if key in table:
        return table[key]
    if _norm_units(CANONICAL_UNITS[canon]) == key:
        return (1.0, 0.0, "amount" if canon == "tp" else None)
    raise UnitError(
        f"'{canon}': units '{units}' are not recognised (accepted: {sorted(table)}); "
        f"set units_{canon} in [prepare] to one of them"
    )


def convert_units(da: xr.DataArray, canon: str) -> tuple[xr.DataArray, str | None]:
    """Convert *da* to canonical units; returns ``(da, kind)`` (kind only for tp)."""
    factor, offset, kind = lookup_conversion(canon, source_units(da, canon))
    out = da if (factor == 1.0 and offset == 0.0) else da * factor + offset
    out.attrs = dict(da.attrs)
    out.attrs["units"] = CANONICAL_UNITS[canon]
    return out, kind


def precipitation_kind(canon_kind: str | None) -> str:
    """Resolve ``[prepare] pr_kind`` against the kind implied by the units."""
    setting = str(getattr(cfg, "pr_kind", "auto")).lower()
    if setting in ("accumulated", "rate"):
        return setting
    return "rate" if canon_kind == "rate" else "accumulated"


# ---------------------------------------------------------------------------
# Grid normalisation
# ---------------------------------------------------------------------------


def normalise_grid(ds: xr.Dataset, lon2d: np.ndarray, lat2d: np.ndarray, ydim: str, xdim: str):
    """Apply the requested longitude convention and latitude order.

    Returns ``(ds, lon2d, lat2d)``.  Regular grids are re-ordered (columns rolled,
    rows flipped) so that coordinates stay monotonic; curvilinear grids only get
    their longitude values wrapped.
    """
    want_lon = str(getattr(cfg, "lon_convention", PM180))
    want_lat = str(getattr(cfg, "lat_order", "keep")).lower()
    regular = is_regular(lon2d, lat2d)

    if want_lon not in (PM180, P360):
        raise ValueError(
            f"[prepare] lon_convention must be '{PM180}' or '{P360}', got '{want_lon}'"
        )
    wrap = to_pm180 if want_lon == PM180 else to_360
    new_lon = wrap(lon2d)

    if regular:
        row = new_lon[0, :]
        if np.any(np.diff(row) < 0):  # wrapped seam inside the domain: roll columns
            k = int(np.argmin(row))
            ds = ds.roll({xdim: -k}, roll_coords=True)
            new_lon = np.roll(new_lon, -k, axis=1)
            lat2d = np.roll(lat2d, -k, axis=1)
        if want_lat in ("descending", "ascending"):
            asc = lat2d[-1, 0] > lat2d[0, 0]
            if (want_lat == "ascending") != asc:
                ds = ds.isel({ydim: slice(None, None, -1)})
                new_lon = new_lon[::-1, :]
                lat2d = lat2d[::-1, :]
    elif want_lat != "keep":
        logger.warning("[prepare] lat_order applies to regular grids only; keeping the row order")

    return ds, new_lon, lat2d


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def output_dir() -> pathlib.Path:
    return pathlib.Path(getattr(cfg, "output_dir", "./data_prepared"))


def output_path(stream: str, year: int) -> pathlib.Path:
    return output_dir() / f"atmotrack_{stream}_{year:04d}.nc"


def canonical_data_source(regular: bool) -> str:
    """The ``[data_source]`` block that reads prepared files."""
    lines = ["[data_source]"]
    if regular:
        lines += ['lat_var   = "latitude"', 'lon_var   = "longitude"', "lat_is_2d = false"]
    else:
        lines += ['lat_var   = "lat"', 'lon_var   = "lon"', "lat_is_2d = true"]
    lines += ['time_var  = "time"']
    for stream in STREAMS:
        lines.append(f'pattern_{stream} = "atmotrack_{stream}_*.nc"')
    for stream, variables in STREAMS.items():
        for canon, key in variables:
            lines.append(f'{key} = "{canon}"')
    return "\n".join(lines) + "\n"


def write_prepared_config(path: pathlib.Path, regular: bool) -> None:
    """Copy the active config with ``data_input``/``[data_source]`` pointing at prepared files."""
    text = cfg.config_path.read_text()
    block = canonical_data_source(regular)
    text = re.sub(r"(?ms)^\[data_source\].*?(?=^\[|\Z)", block + "\n", text)
    text = re.sub(
        r"(?m)^data_input\s*=.*$",
        f'data_input    = "{output_dir().resolve()}"',
        text,
    )
    path.write_text(text)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def configured_streams() -> list[str]:
    """Streams whose ``pattern_<stream>`` key is set in the config."""
    return [s for s in STREAMS if getattr(cfg, f"pattern_{s}", None)]


def prepare_year(year: int, streams: list[str] | None = None, force: bool = False) -> dict:
    """Write the canonical files of *year* for every requested stream.

    Returns ``{stream: path}`` for the files written (or already present).
    """
    streams = streams or configured_streams()
    out: dict[str, pathlib.Path] = {}
    output_dir().mkdir(parents=True, exist_ok=True)
    dt = float(cfg.DT)

    for stream in streams:
        path = output_path(stream, year)
        if path.exists() and not force:
            logger.info(f"{path.name} exists, skipping (use --force to rewrite)")
            out[stream] = path
            continue
        try:
            ds = open_years(f"pattern_{stream}", year)
        except FileNotFoundError as exc:
            logger.warning(f"{stream} {year}: {exc}")
            continue
        times = timeaxis.load_times(ds, cfg.time_var)
        lon2d, lat2d = load_grid(ds)
        ydim, xdim = spatial_dims(ds)
        ds, lon2d, lat2d = normalise_grid(ds, lon2d, lat2d, ydim, xdim)
        grid = Grid(lon2d, lat2d)

        data_vars = {}
        out_times = None
        for canon, key in STREAMS[stream]:
            name = getattr(cfg, key, None)
            if not name or name not in ds:
                logger.warning(
                    f"{stream} {year}: variable '{name}' ({key}) not in the files, skipped"
                )
                continue
            da = ensure_tyx(ds[name], ydim, xdim)
            da, kind = convert_units(da, canon)
            if stream in ACCUMULATED_STREAMS:
                total, _ = accumulate_to_dt(da, dt, kind=precipitation_kind(kind))
                da = total
            else:
                step = timeaxis.step_hours(times)
                if not np.isnan(step) and step > dt and getattr(cfg, "interpolate_time", False):
                    da = _interpolate_time(da, dt)
                else:
                    da = subsample_to_dt(da, dt)
            da = da.rename(canon)
            da.attrs["long_name"] = LONG_NAMES[canon]
            data_vars[canon] = da
            out_times = da[cfg.time_var]

        if not data_vars:
            logger.warning(f"{stream} {year}: nothing to write")
            continue

        new = _build_dataset(data_vars, out_times, grid, ydim, xdim)
        new.attrs.update(
            {
                "Conventions": "CF-1.8",
                "source": "atmotrack-prepare",
                "atmotrack_stream": stream,
                "time_step_hours": dt,
            }
        )
        encoding = {v: {"zlib": True, "complevel": 4, "dtype": "float32"} for v in data_vars}
        encoding["time"] = timeaxis.encode_times(timeaxis.load_times(new, "time"))
        tmp = path.with_suffix(".nc.tmp")
        new.to_netcdf(tmp, mode="w", format="NETCDF4", encoding=encoding)
        tmp.replace(path)
        logger.info(f"wrote {path} ({timeaxis.describe(timeaxis.load_times(new, 'time'))})")
        out[stream] = path
        ds.close()
    return out


def _interpolate_time(da: xr.DataArray, dt: float) -> xr.DataArray:
    times = timeaxis.load_times(da.to_dataset(name="_v"), cfg.time_var)
    if not isinstance(times, __import__("pandas").DatetimeIndex):
        raise ValueError("interpolate_time is only supported for the standard calendar")
    new_times = __import__("pandas").date_range(times[0], times[-1], freq=f"{dt:g}h")
    return da.interp({cfg.time_var: new_times})


def _build_dataset(data_vars: dict, out_times, grid: Grid, ydim: str, xdim: str) -> xr.Dataset:
    tvals = np.asarray(out_times.values)
    if grid.is_regular:
        dims = ("time", "latitude", "longitude")
        coords = {
            "time": ("time", tvals),
            "latitude": (
                "latitude",
                grid.lat_1d,
                {"units": "degrees_north", "standard_name": "latitude"},
            ),
            "longitude": (
                "longitude",
                grid.lon_1d,
                {"units": "degrees_east", "standard_name": "longitude"},
            ),
        }
    else:
        dims = ("time", "y", "x")
        coords = {
            "time": ("time", tvals),
            "lat": (("y", "x"), grid.lat, {"units": "degrees_north", "standard_name": "latitude"}),
            "lon": (("y", "x"), grid.lon, {"units": "degrees_east", "standard_name": "longitude"}),
        }
    variables = {
        name: (dims, np.asarray(da.values, dtype=np.float32), dict(da.attrs))
        for name, da in data_vars.items()
    }
    return xr.Dataset(variables, coords=coords)
