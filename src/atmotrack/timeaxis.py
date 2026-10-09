"""Time axis helpers that work for standard and non-standard (cftime) calendars.

``load_times`` returns a :class:`pandas.DatetimeIndex` for the standard /
proleptic Gregorian calendars and an :class:`xarray.CFTimeIndex` otherwise
(``noleap``, ``360_day``, …).  Both support ``.year``, ``.hour``,
``times[i].strftime(...)`` and subtraction into timedeltas, which is all the
trackers need.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd
import xarray as xr

STANDARD_CALENDARS = {"standard", "gregorian", "proleptic_gregorian"}


def load_times(ds: xr.Dataset, time_var: str):
    """Time index of *ds* as a DatetimeIndex or CFTimeIndex."""
    coord = ds[time_var]
    values = coord.values
    if np.issubdtype(values.dtype, np.datetime64):
        return pd.DatetimeIndex(values)
    if values.dtype == object and values.size and hasattr(values.flat[0], "calendar"):
        return xr.CFTimeIndex(values)
    raise TypeError(
        f"Time coordinate '{time_var}' is not decoded (dtype {values.dtype}). "
        "Open the dataset with decode_times=True."
    )


def calendar_of(times) -> str:
    """CF calendar name of a DatetimeIndex ('standard') or CFTimeIndex."""
    if isinstance(times, pd.DatetimeIndex):
        return "standard"
    cal = getattr(times, "calendar", None)
    if cal is None and len(times):
        cal = times[0].calendar
    return cal or "standard"


def is_standard_calendar(times) -> bool:
    return calendar_of(times) in STANDARD_CALENDARS


def hours_between(later, earlier) -> float:
    """Hours from *earlier* to *later* (datetime, Timestamp or cftime objects)."""
    return (later - earlier).total_seconds() / 3600.0


def step_hours(times, *, strict: bool = False) -> float:
    """Dominant time step [h] of a monotonic index.

    Returns the most common difference.  With ``strict=True`` an irregular axis
    raises ``ValueError``.  A single time stamp returns ``nan``.
    """
    if len(times) < 2:
        return float("nan")
    secs = np.array([(times[i + 1] - times[i]).total_seconds() for i in range(len(times) - 1)])
    hours = np.round(secs / 3600.0, 6)
    counts = Counter(hours.tolist())
    step, n = counts.most_common(1)[0]
    if strict and n != len(hours):
        raise ValueError(f"Irregular time axis: steps {sorted(counts)} h")
    if step <= 0:
        raise ValueError("Time axis is not increasing")
    return float(step)


def is_regular(times) -> bool:
    try:
        step_hours(times, strict=True)
        return True
    except ValueError:
        return False


def years(times) -> np.ndarray:
    """Calendar year of every time stamp."""
    return np.asarray(times.year, dtype=int)


def on_step_mask(times, dt_h: float) -> np.ndarray:
    """Boolean mask of the time stamps that fall on the ``dt_h``-hourly grid of each day."""
    hod = np.asarray(times.hour, dtype=float) + np.asarray(times.minute, dtype=float) / 60.0
    return np.isclose(np.mod(hod, dt_h), 0.0) | np.isclose(np.mod(hod, dt_h), dt_h)


def encode_times(times) -> dict:
    """NetCDF encoding for a time coordinate built from *times*."""
    if isinstance(times, pd.DatetimeIndex):
        return {
            "units": "hours since 1900-01-01 00:00:00",
            "calendar": "standard",
            "dtype": "int64",
        }
    first = times[0]
    return {
        "units": f"hours since {first.year:04d}-01-01 00:00:00",
        "calendar": calendar_of(times),
        "dtype": "int64",
    }


def describe(times) -> str:
    if len(times) == 0:
        return "empty"
    step = step_hours(times)
    reg = "" if is_regular(times) else " (irregular)"
    return (
        f"{len(times)} steps, {times[0].strftime('%Y-%m-%d %H:%M')} .. "
        f"{times[-1].strftime('%Y-%m-%d %H:%M')}, step {step:g} h{reg}, calendar {calendar_of(times)}"
    )
