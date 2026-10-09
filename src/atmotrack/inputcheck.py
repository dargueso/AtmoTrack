"""Check an input collection against what the trackers need.

For every configured stream the checker reports the files found, the time
axis (calendar, step, gaps, year coverage), the grid, the variables with
their units, and whether the stream can be read directly (``ready``), needs
``atmotrack-prepare`` (``prepare``) or cannot be used (``unsupported``).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

import numpy as np
import xarray as xr

from atmotrack import config as cfg
from atmotrack import timeaxis
from atmotrack.grid import MIXED, PM180, Grid
from atmotrack.io import FileEntry, index_pattern, load_grid, spatial_dims
from atmotrack.prepare import (
    ACCUMULATED_STREAMS,
    CANONICAL_UNITS,
    STREAMS,
    UnitError,
    lookup_conversion,
    source_units,
)

READY, PREPARE, UNSUPPORTED, MISSING = "ready", "prepare", "unsupported", "missing"
_RANK = {READY: 0, PREPARE: 1, UNSUPPORTED: 2, MISSING: 2}


@dataclass
class VariableReport:
    canon: str
    key: str
    name: str | None
    present: bool
    units: str | None = None
    conversion: str | None = None  # e.g. "x100" or "ok"
    status: str = READY
    note: str = ""


@dataclass
class StreamReport:
    stream: str
    pattern: str
    files: list[FileEntry] = field(default_factory=list)
    status: str = READY
    notes: list[str] = field(default_factory=list)
    calendar: str | None = None
    step_h: float | None = None
    years: tuple[int, int] | None = None
    gaps: int = 0
    grid: str | None = None
    grid_regular: bool | None = None
    dims: tuple[str, ...] | None = None
    extra_dims: list[str] = field(default_factory=list)
    lon_convention: str | None = None
    lat_order: str | None = None
    variables: list[VariableReport] = field(default_factory=list)
    _lon: np.ndarray | None = field(default=None, repr=False)
    _lat: np.ndarray | None = field(default=None, repr=False)

    def flag(self, status: str, note: str) -> None:
        if _RANK[status] > _RANK[self.status]:
            self.status = status
        if note not in self.notes:
            self.notes.append(note)

    def as_dict(self) -> dict:
        return {
            "stream": self.stream,
            "pattern": self.pattern,
            "n_files": len(self.files),
            "status": self.status,
            "notes": self.notes,
            "calendar": self.calendar,
            "step_h": self.step_h,
            "years": list(self.years) if self.years else None,
            "gaps": self.gaps,
            "grid": self.grid,
            "grid_regular": self.grid_regular,
            "dims": list(self.dims) if self.dims else None,
            "extra_dims": self.extra_dims,
            "lon_convention": self.lon_convention,
            "lat_order": self.lat_order,
            "variables": [v.__dict__ for v in self.variables],
        }


def _iso_plus(iso: str, hours: float) -> str:
    t = _dt.datetime.fromisoformat(iso) + _dt.timedelta(hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def check_stream(stream: str, reindex: bool = False) -> StreamReport:
    pattern_key = f"pattern_{stream}"
    pattern = getattr(cfg, pattern_key, None)
    rep = StreamReport(stream=stream, pattern=pattern or "")
    if not pattern:
        rep.flag(MISSING, f"{pattern_key} not set in config.toml")
        return rep
    try:
        rep.files = index_pattern(pattern_key, reindex=reindex)
    except FileNotFoundError as exc:
        rep.flag(MISSING, str(exc))
        return rep
    except (KeyError, TypeError, ValueError) as exc:
        rep.flag(UNSUPPORTED, str(exc))
        return rep

    dt = float(cfg.DT)
    # ---- time axis across files
    steps = {e.step_h for e in rep.files if not np.isnan(e.step_h)}
    calendars = {e.calendar for e in rep.files}
    rep.calendar = ", ".join(sorted(calendars))
    if len(calendars) > 1:
        rep.flag(UNSUPPORTED, f"files use different calendars: {sorted(calendars)}")
    if len(steps) > 1:
        rep.flag(UNSUPPORTED, f"files use different time steps: {sorted(steps)} h")
    rep.step_h = min(steps) if steps else None
    rep.years = (int(rep.files[0].t0[:4]), int(rep.files[-1].t1[:4]))
    if rep.step_h:
        for a, b in zip(rep.files, rep.files[1:]):
            expected = _iso_plus(a.t1, rep.step_h)
            if b.t0 > expected:
                rep.gaps += 1
            elif b.t0 < expected:
                rep.flag(PREPARE, f"{a.path} and {b.path} overlap in time")
        if rep.gaps:
            rep.flag(PREPARE, f"{rep.gaps} gap(s) between consecutive files")
        if stream in ACCUMULATED_STREAMS:
            ratio = dt / rep.step_h
            if ratio < 1 or not np.isclose(ratio, round(ratio)):
                rep.flag(UNSUPPORTED, f"step {rep.step_h:g} h does not divide DT={dt:g} h")
            elif ratio > 1:
                rep.flag(PREPARE, f"accumulate {rep.step_h:g} h -> {dt:g} h")
        else:
            ratio = dt / rep.step_h
            if np.isclose(ratio, 1):
                pass
            elif ratio > 1 and np.isclose(ratio, round(ratio)):
                rep.flag(PREPARE, f"subsample {rep.step_h:g} h -> {dt:g} h")
            elif ratio < 1 and getattr(cfg, "interpolate_time", False):
                rep.flag(PREPARE, f"interpolate {rep.step_h:g} h -> {dt:g} h")
            else:
                rep.flag(
                    UNSUPPORTED,
                    f"step {rep.step_h:g} h is coarser than DT={dt:g} h "
                    "(set [prepare] interpolate_time = true or increase DT)",
                )
    if (
        rep.calendar
        and not timeaxis.is_standard_calendar(type("T", (), {"calendar": rep.calendar})())
        and rep.calendar not in timeaxis.STANDARD_CALENDARS
    ):
        rep.notes.append(f"non-standard calendar '{rep.calendar}' (supported)")

    # ---- grid and variables from the first file
    try:
        with xr.open_dataset(rep.files[0].path, decode_times=True) as ds:
            _check_grid_and_vars(rep, ds, stream)
    except Exception as exc:  # noqa: BLE001 - report instead of crash
        rep.flag(UNSUPPORTED, f"cannot read {rep.files[0].path}: {exc}")
    return rep


def _check_grid_and_vars(rep: StreamReport, ds: xr.Dataset, stream: str) -> None:
    for name in (cfg.lat_var, cfg.lon_var):
        if name not in ds.variables:
            rep.flag(
                UNSUPPORTED, f"coordinate '{name}' not found (variables: {list(ds.variables)})"
            )
            return
    lon, lat = load_grid(ds)
    grid = Grid(lon, lat)
    rep._lon, rep._lat = lon, lat
    rep.grid = grid.describe()
    rep.grid_regular = grid.is_regular
    rep.lon_convention = grid.lon_convention
    rep.lat_order = "ascending" if grid.lat_ascending else "descending"
    want_lon = str(getattr(cfg, "lon_convention", PM180))
    if grid.lon_convention == MIXED:
        rep.flag(UNSUPPORTED, "longitudes are neither in -180..180 nor in 0..360")
    elif grid.is_regular and grid.lon_convention != want_lon:
        rep.flag(PREPARE, f"longitudes in {grid.lon_convention}; trackers use {want_lon}")
    if not grid.is_regular:
        rep.notes.append("curvilinear grid: winds must be earth-relative (no rotation is applied)")
    ydim, xdim = spatial_dims(ds)

    for canon, key in STREAMS[stream]:
        name = getattr(cfg, key, None)
        v = VariableReport(canon=canon, key=key, name=name, present=bool(name and name in ds))
        rep.variables.append(v)
        if not v.present:
            v.status = MISSING
            v.note = f"{key} = '{name}' not in file"
            optional = canon in ("q", "v") and stream in ("z850", "z200")
            rep.flag(PREPARE if optional else UNSUPPORTED, f"variable '{name}' ({key}) missing")
            continue
        da = ds[name]
        rep.dims = tuple(da.dims)
        extra = [d for d in da.dims if d not in (cfg.time_var, ydim, xdim)]
        bad = [d for d in extra if da.sizes[d] != 1]
        if bad:
            rep.flag(UNSUPPORTED, f"'{name}' has extra dimension(s) {bad} (select a level first)")
        elif extra:
            rep.extra_dims = extra
            rep.flag(PREPARE, f"size-1 dimension(s) {extra} will be squeezed")
        if "stagger" in da.attrs and da.attrs["stagger"]:
            rep.flag(UNSUPPORTED, f"'{name}' is on a staggered grid (destagger first)")
        if da.dims[-2:] != (ydim, xdim) or (cfg.time_var in da.dims and da.dims[0] != cfg.time_var):
            rep.flag(PREPARE, f"'{name}' dims {da.dims} will be reordered to (time, y, x)")
        v.units = source_units(da, canon)
        try:
            factor, offset, kind = lookup_conversion(canon, v.units)
            if factor == 1.0 and offset == 0.0 and kind != "rate":
                v.conversion = "ok"
            else:
                v.conversion = (
                    f"x{factor:g}"
                    + (f" +{offset:g}" if offset else "")
                    + (f" ({kind})" if kind else "")
                )
                v.status = PREPARE
                rep.flag(
                    PREPARE, f"'{name}': {v.units} -> {CANONICAL_UNITS[canon]} ({v.conversion})"
                )
        except UnitError as exc:
            v.status = UNSUPPORTED
            v.note = str(exc)
            rep.flag(UNSUPPORTED, str(exc))


def cross_checks(reports: list[StreamReport]) -> list[str]:
    """Consistency notes across streams (grid and calendar)."""
    notes = []
    ref = next((r for r in reports if r._lon is not None), None)
    if ref is None:
        return notes
    for r in reports:
        if r._lon is None or r is ref:
            continue
        if r._lon.shape != ref._lon.shape or not (
            np.allclose(r._lon, ref._lon, atol=1e-4) and np.allclose(r._lat, ref._lat, atol=1e-4)
        ):
            r.flag(UNSUPPORTED, f"grid differs from {ref.stream} (regrid to a common grid first)")
            notes.append(f"{r.stream}: grid differs from {ref.stream}")
        if r.calendar and ref.calendar and r.calendar != ref.calendar:
            r.flag(UNSUPPORTED, f"calendar differs from {ref.stream}")
            notes.append(f"{r.stream}: calendar differs from {ref.stream}")
    return notes


def check_all(streams: list[str] | None = None, reindex: bool = False) -> list[StreamReport]:
    streams = streams or [s for s in STREAMS if getattr(cfg, f"pattern_{s}", None)]
    reports = [check_stream(s, reindex=reindex) for s in streams]
    cross_checks(reports)
    return reports


def overall_status(reports: list[StreamReport]) -> str:
    present = [r for r in reports if r.status != MISSING]
    if not present:
        return MISSING
    return max((r.status for r in present), key=lambda s: _RANK[s])


def format_report(reports: list[StreamReport]) -> str:
    lines = [
        f"AtmoTrack input check — config {cfg.config_path}",
        f"data_input = {cfg.data_input}",
        f"DT = {cfg.DT:g} h",
        "",
    ]
    for r in reports:
        lines.append(
            f"[{r.stream}]  {r.status.upper()}   pattern {r.pattern!r}, {len(r.files)} file(s)"
        )
        if r.files:
            yrs = f"{r.years[0]}–{r.years[1]}" if r.years else "?"
            lines.append(
                f"    time: step {r.step_h:g} h, calendar {r.calendar}, years {yrs}, gaps {r.gaps}"
            )
        if r.grid:
            lines.append(f"    grid: {r.grid}")
        for v in r.variables:
            if v.present:
                lines.append(
                    f"    {v.canon:5s} = {v.name!r:12s} units {str(v.units)!r:14s} {v.conversion or ''}"
                )
            else:
                lines.append(f"    {v.canon:5s} = {v.name!r:12s} MISSING")
        for n in r.notes:
            lines.append(f"    - {n}")
        lines.append("")
    status = overall_status(reports)
    verdict = {
        READY: "READY: the trackers can read these files directly.",
        PREPARE: "PREPARE: run `atmotrack-prepare` to write canonical files (or let the trackers align on the fly where noted).",
        UNSUPPORTED: "UNSUPPORTED: fix the items marked above before tracking.",
        MISSING: "No input streams found; check [paths] data_input and the pattern_* keys.",
    }[status]
    lines.append(verdict)
    return "\n".join(lines)
