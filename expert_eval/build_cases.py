#!/usr/bin/env python
"""
build_cases.py — Build the case pool for the DANA expert evaluation site.

Reads the COL tracking output (data_tracking/col_z500_YYYY.nc), re-evaluates the
COL criteria for every z500 cyclone object and time step, classifies time steps
into clear / borderline COL and non-COL cases, selects a stratified pool and
writes, for each selected case:

    cases/frames/YYYYMMDDHH.png      maps without objects (central time +/- loop)
    cases/overlays/<case_id>.png     transparent overlay with the algorithm COLs
    cases/eval/<case_id>.json        private evaluation data (masks, criteria)
    cases/eval/fields/<case_id>.npz  z500/u200 fields for offline diagnostics
    cases/manifest.json              public case list and map geometry

Re-running appends new cases; existing case ids are never changed.

Usage
-----
    python build_cases.py                       # all finished years, 600 new cases
    python build_cases.py --years 1970 --n-cases 40 --jobs 4
    python build_cases.py --high-impact high_impact_events.toml --target-total 1000
        # one case per high-impact event, then stratified cases up to 1000 in total
    python build_cases.py --refresh --high-impact high_impact_events.toml
        # after tuning and re-running the tracking: recompute the algorithm data of every existing
        # case (categories, tags, detections, overlays); maps and case ids stay the same
"""

import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import logging
import os
import pathlib
import pickle
import random
import subprocess
import sys
import time
import tomllib
import zlib
from bisect import bisect_left
from collections import defaultdict

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import matplotlib  # noqa: E402

matplotlib.use("Agg")

import cartopy.crs as ccrs  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import xarray as xr  # noqa: E402
from criteria import ColParams, col_step_criteria, finalize_failures, grid_spacing  # noqa: E402
from joblib import Parallel, delayed  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from PIL import Image  # noqa: E402
from scipy import ndimage  # noqa: E402
from settings import load_settings  # noqa: E402

import atmotrack_config as cfg  # noqa: E402
from constants import const  # noqa: E402
from utils import get_logger  # noqa: E402

SCAN_VERSION = 1
logger = get_logger("expert_eval", level=logging.INFO)

POS_BORDER_TAGS = ["short_lived", "onset", "decay", "marginal_isolation", "near_box_edge"]
NEG_BORDER_TAGS = [
    "fail:isolation",
    "fail:eastward_flow",
    "fail:region",
    "fail:duration",
    "fail:object_bounds",
    "fail:border",
    "pre_onset",
    "post_decay",
    "marginal_isolation_fail",
    "near_box_outside",
]
SEASONS = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
           6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}  # fmt: skip


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def encode_mask(mask):
    """Bool 2-D array -> base64(zlib(packbits)), decoded by evaluate.decode_mask."""
    packed = np.packbits(np.asarray(mask, dtype=bool).ravel())
    return base64.b64encode(zlib.compress(packed.tobytes(), 9)).decode("ascii")


def git_hash():
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return None


def tracking_files(years=None, settle_minutes=10, force=False):
    data_dir = pathlib.Path(cfg.data_tracking)
    if not data_dir.is_absolute():  # config paths are relative to the repo root
        data_dir = REPO / data_dir
    files = sorted(data_dir.glob("col_z500_????.nc"))
    now = time.time()
    out = {}
    for f in files:
        year = int(f.stem[-4:])
        if years and year not in years:
            continue
        if not force and now - f.stat().st_mtime < settle_minutes * 60:
            logger.warning(f"Skipping {f.name}: modified less than {settle_minutes} min ago")
            continue
        out[year] = f
    return out


def algo_version(params, files):
    """Fingerprint of what the algorithm answer depends on: code, thresholds and tracking files."""
    try:  # last commit that touched the algorithm code (unrelated commits keep the version)
        code = subprocess.check_output(
            [
                "git",
                "-C",
                str(REPO),
                "log",
                "-1",
                "--format=%h",
                "--",
                "tracking",
                "tracking_functions.py",
            ],
            text=True,
        ).strip()
    except Exception:
        code = None
    src = {
        "algorithm_commit": code,
        "col_params": params.asdict(),
        "tracking": sorted((y, f.stat().st_mtime_ns, f.stat().st_size) for y, f in files.items()),
    }
    return hashlib.sha1(json.dumps(src, sort_keys=True).encode()).hexdigest()[:10]


def open_grid(ds):
    lat = ds.latitude.values.astype(float)
    lon = ds.longitude.values.astype(float)
    lon2d, lat2d = np.meshgrid(lon, lat)
    return lat, lon, lat2d, lon2d


# ---------------------------------------------------------------------------
# Phase 1: scan a year (criteria records + candidate classification)
# ---------------------------------------------------------------------------
def scan_year(year, path, params, S, cache_dir):
    stat = path.stat()
    key = {
        "v": SCAN_VERSION,
        "mtime": stat.st_mtime_ns,
        "size": stat.st_size,
        "params": params.asdict(),
        "build": {k: v for k, v in S["build"].items() if not k.startswith("fig") and k != "dpi"},
    }
    cache = cache_dir / f"scan_{year:04d}.pkl"
    if cache.exists():
        try:
            with open(cache, "rb") as f:
                data = pickle.load(f)
            if data["key"] == key:
                return data
        except Exception:
            pass

    log = get_logger("expert_eval", level=logging.INFO)
    t0 = time.time()
    B = S["build"]
    with xr.open_dataset(path) as ds:
        times = pd.DatetimeIndex(ds.time.values)
        lat, lon, lat2d, lon2d = open_grid(ds)
        cy = ds.cy_z500_objects.values.astype(np.int32)
        col = ds.col_objects.values.astype(np.int32)
        z500 = ds.z500.values
        u200 = ds.u200.values
    nt, ny, nx = cy.shape
    dt_hours = int(round((times[1] - times[0]).total_seconds() / 3600))
    area2d, gs = grid_spacing(lat2d, lon2d)
    buf = int(params.col_buffer / gs)

    # ---- criteria records for every cyclone object / time step
    records = {}  # (t, id) -> record
    for iobj, sl in enumerate(ndimage.find_objects(cy)):
        if sl is None:
            continue
        oid = iobj + 1
        t_start, t_stop = sl[0].start, sl[0].stop
        la_s, la_e = max(sl[1].start - buf, 0), min(sl[1].stop + buf, ny)
        lo_s, lo_e = max(sl[2].start - buf, 0), min(sl[2].stop + buf, nx)
        cy_life = t_stop - t_start
        obj_recs = []
        for tt in range(cy_life):
            t = t_start + tt
            obj2d = cy[t, la_s:la_e, lo_s:lo_e] == oid
            if not obj2d.any():
                continue
            rec = col_step_criteria(
                obj2d,
                z500[t, la_s:la_e, lo_s:lo_e],
                u200[t, la_s:la_e, lo_s:lo_e],
                lat2d[la_s:la_e, lo_s:lo_e],
                lon2d[la_s:la_e, lo_s:lo_e],
                (la_s, la_e, lo_s, lo_e),
                (ny, nx),
                cy_life,
                params,
                gs,
                area2d[la_s:la_e, lo_s:lo_e],
            )
            rec["id"] = oid
            rec["t"] = t
            rec["cy_life_steps"] = cy_life
            rec["step_in_cy_life"] = tt
            rec["saved_col"] = bool((col[t, la_s:la_e, lo_s:lo_e][obj2d] == oid).any())
            obj_recs.append(rec)
        # COL duration after all step criteria (cy_duration handled separately)
        passing = [
            r["t"]
            for r in obj_recs
            if all(v["pass"] for k, v in r["criteria"].items() if k != "cy_duration")
        ]
        ext = (passing[-1] - passing[0] + 1) if passing else 0
        for r in obj_recs:
            r["criteria"]["col_duration"] = {
                "pass": not (ext < params.min_steps),
                "value": ext,
                "threshold": params.min_steps,
                "margin": ext - params.min_steps,
            }
            r["would_be_col"] = bool(
                r["step_pass"] and passing and passing[0] <= r["t"] <= passing[-1]
                and r["criteria"]["col_duration"]["pass"]
            )  # fmt: skip
            finalize_failures(r)
            records[(r["t"], oid)] = r

    # ---- saved COL lifetimes (the answer the site evaluates against)
    col_life = {}
    for iobj, sl in enumerate(ndimage.find_objects(col)):
        if sl is None:
            continue
        cid = iobj + 1
        present = [
            sl[0].start + k
            for k in range(sl[0].stop - sl[0].start)
            if (col[sl[0].start + k, sl[1], sl[2]] == cid).any()
        ]
        if present:
            col_life[cid] = (present[0], present[-1])

    n_mismatch = sum(1 for r in records.values() if r["would_be_col"] != r["saved_col"])
    col_ids_at = [np.unique(col[t])[1:].tolist() for t in range(nt)]

    # ---- classify time steps
    box = params.col_region
    e = B["expand_box_deg"]
    ebox = [box[0] - e, box[1] + e, box[2] - e, box[3] + e]
    nloop = int(round(B["loop_hours"] / dt_hours))
    min_life_h = params.min_steps * dt_hours
    thr = params.col_thres_isolation
    recs_at = defaultdict(list)
    for (t, _), r in records.items():
        recs_at[t].append(r)

    candidates = []
    for t in range(nloop, nt - nloop):
        tags = set()
        events = []
        ts = times[t]
        if col_ids_at[t]:
            clear = True
            for cid in col_ids_at[t]:
                first, last = col_life.get(cid, (t, t))
                life_h = (last - first + 1) * dt_hours
                events.append(f"{year}-{cid}")
                if life_h <= min_life_h + B["short_lived_extra_hours"]:
                    tags.add("short_lived")
                if (t - first) * dt_hours < B["onset_decay_hours"]:
                    tags.add("onset")
                if (last - t) * dt_hours < B["onset_decay_hours"]:
                    tags.add("decay")
                r = records.get((t, cid))
                if r:
                    iso = r["criteria"]["isolation"]
                    if (iso.get("value") or 0) < B["marginal_ring_fraction"] or (
                        iso.get("dz_at_required_fraction") or 0
                    ) < thr + B["marginal_dz_extra_m"]:
                        tags.add("marginal_isolation")
                    if (r["criteria"]["region"].get("margin") or 0) < B["near_box_edge_deg"]:
                        tags.add("near_box_edge")
                clear = clear and not tags
            category = "col_clear" if clear else "col_borderline"
        else:
            near = [
                r
                for r in recs_at.get(t, [])
                if ebox[0] <= r["zmin_lon"] <= ebox[1] and ebox[2] <= r["zmin_lat"] <= ebox[3]
            ]
            for r in near:
                events.append(f"{year}-{r['id']}")
                tags.add("cyclone_not_col")
                ff = r["first_failed_criterion"]
                if ff in ("cy_duration", "col_duration"):
                    ff = "duration"
                if ff:
                    tags.add(f"fail:{ff}")
                if r["id"] in col_life:
                    first, last = col_life[r["id"]]
                    if 0 < (first - t) * dt_hours <= B["onset_decay_hours"]:
                        tags.add("pre_onset")
                    if 0 < (t - last) * dt_hours <= B["onset_decay_hours"]:
                        tags.add("post_decay")
                iso = r["criteria"]["isolation"]
                if not iso["pass"] and (
                    (iso.get("value") or 0) >= B["fail_ring_fraction"]
                    or (iso.get("dz_at_required_fraction") or -1e9) >= thr - B["fail_dz_deficit_m"]
                ):
                    tags.add("marginal_isolation_fail")
                reg = r["criteria"]["region"]
                if not reg["pass"] and (reg.get("margin") or -1e9) >= -B["near_box_outside_deg"]:
                    tags.add("near_box_outside")
            category = "nocol_borderline" if near else "nocol_clear"
        candidates.append(
            {
                "case_id": ts.strftime("%Y%m%d%H"),
                "year": year,
                "t": t,
                "time": ts.isoformat(),
                "month": int(ts.month),
                "season": SEASONS[int(ts.month)],
                "category": category,
                "tags": sorted(tags),
                "events": events,
            }
        )

    data = {
        "key": key,
        "year": year,
        "dt_hours": dt_hours,
        "records": records,
        "col_life": col_life,
        "candidates": candidates,
        "n_records": len(records),
        "n_mismatch": n_mismatch,
    }
    with open(cache, "wb") as f:
        pickle.dump(data, f, protocol=5)
    log.info(
        f"Scanned {year}: {len(records)} cyclone records, {len(col_life)} COLs, "
        f"criteria/saved mismatches {n_mismatch} ({time.time() - t0:.0f} s)"
    )
    return data


# ---------------------------------------------------------------------------
# Phase 2: stratified selection
# ---------------------------------------------------------------------------
class GapChecker:
    def __init__(self, gap_any_h, gap_event_h, repeat_events=False):
        self.repeat_events = (
            repeat_events  # allow several cases per event/category (gap still applies)
        )
        self.gap_any = np.timedelta64(int(gap_any_h * 3600), "s")
        self.gap_event = np.timedelta64(int(gap_event_h * 3600), "s")
        self.times = []
        self.event_times = defaultdict(list)
        self.event_cat = set()

    @staticmethod
    def _too_close(sorted_times, t, gap):
        i = bisect_left(sorted_times, t)
        for j in (i - 1, i):
            if 0 <= j < len(sorted_times) and abs(sorted_times[j] - t) < gap:
                return True
        return False

    def ok(self, c):
        t = np.datetime64(c["time"])
        if self._too_close(self.times, t, self.gap_any):
            return False
        for ev in c["events"]:
            if not self.repeat_events and (ev, c["category"]) in self.event_cat:
                return False
            if self._too_close(self.event_times[ev], t, self.gap_event):
                return False
        return True

    def add(self, c):
        t = np.datetime64(c["time"])
        self.times.insert(bisect_left(self.times, t), t)
        for ev in c["events"]:
            lst = self.event_times[ev]
            lst.insert(bisect_left(lst, t), t)
            self.event_cat.add((ev, c["category"]))


CATEGORIES = ("col_clear", "col_borderline", "nocol_clear", "nocol_borderline")


def quotas_for(n, B):
    """Split n cases over the categories with settings [build] quota_* (largest remainder)."""
    raw = {cat: n * B[f"quota_{cat}"] for cat in CATEGORIES}
    q = {cat: int(v) for cat, v in raw.items()}
    for cat in sorted(CATEGORIES, key=lambda c: raw[c] - q[c], reverse=True)[: n - sum(q.values())]:
        q[cat] += 1
    return q


def select_cases(candidates, quotas, B, existing, rng, repeat_events=False):
    """Stratified selection; `quotas` maps category -> number of new cases."""
    checker = GapChecker(B["min_gap_hours_any"], B["min_gap_hours_same_event"], repeat_events)
    for c in existing:
        checker.add(c)
    taken = {c["case_id"] for c in existing}
    chosen = []
    for cat in CATEGORIES:
        quota = quotas.get(cat, 0)
        pool = [c for c in candidates if c["category"] == cat and c["case_id"] not in taken]
        if cat.endswith("borderline"):
            keys = POS_BORDER_TAGS if cat.startswith("col") else NEG_BORDER_TAGS
            buckets = {k: [c for c in pool if k in c["tags"]] for k in keys}
        else:
            buckets = {
                s: [c for c in pool if c["season"] == s] for s in ("DJF", "MAM", "JJA", "SON")
            }
        for b in buckets.values():
            rng.shuffle(b)
        buckets = {k: v for k, v in buckets.items() if v}
        got = 0
        while got < quota and buckets:
            for k in list(buckets):
                b = buckets[k]
                while b:
                    c = b.pop()
                    if c["case_id"] not in taken and checker.ok(c):
                        chosen.append(c)
                        taken.add(c["case_id"])
                        checker.add(c)
                        got += 1
                        break
                if not b:
                    del buckets[k]
                if got >= quota:
                    break
        logger.info(f"  {cat:17s}: {got}/{quota} selected (pool {len(pool)})")
    return chosen


# ---------------------------------------------------------------------------
# High-impact events (forced cases)
# ---------------------------------------------------------------------------
def choose_event_time(maps, dt_hours, loop_hours):
    """Case time whose +/-loop covers most slide maps; then prefer a slide map, the midpoint of
    the covered maps, and finally the later time."""
    ts = sorted(np.datetime64(m) for m in maps)
    step = np.timedelta64(dt_hours, "h")
    loop = np.timedelta64(loop_hours, "h")
    best = None
    t = ts[0] - loop
    while t <= ts[-1] + loop:
        covered = [m for m in ts if abs(m - t) <= loop]
        if covered:
            mid = covered[0] + (covered[-1] - covered[0]) / 2
            key = (len(covered), t in ts, -abs(t - mid), t)
            if best is None or key > best[0]:
                best = (key, t)
        t += step
    return best[1]


def load_high_impact(path, dt_hours, loop_hours):
    with open(path, "rb") as f:
        events = tomllib.load(f)["event"]
    out = []
    for ev in events:
        t = (
            np.datetime64(ev["case_time"])
            if ev.get("case_time")
            else choose_event_time(ev["maps"], dt_hours, loop_hours)
        )
        cid = pd.Timestamp(t).strftime("%Y%m%d%H")
        out.append({"id": ev["id"], "maps": ev["maps"], "case_id": cid, "case_time": str(t)})
    return out


# ---------------------------------------------------------------------------
# Phase 3: rendering and evaluation data
# ---------------------------------------------------------------------------
def make_figure(S, lon, lat):
    B = S["build"]
    fig = plt.figure(figsize=(B["fig_width_in"], B["fig_height_in"]), dpi=B["dpi"])
    ax = fig.add_axes([0.08, 0.12, 0.86, 0.85], projection=ccrs.PlateCarree())
    ax.set_extent([lon.min(), lon.max(), lat.min(), lat.max()], crs=ccrs.PlateCarree())
    return fig, ax


def map_geometry(S, lon, lat):
    fig, ax = make_figure(S, lon, lat)
    fig.canvas.draw()
    pos = ax.get_position()
    w, h = fig.canvas.get_width_height()
    plt.close(fig)
    return {
        "img_w": w,
        "img_h": h,
        "ax_left": pos.x0,
        "ax_right": pos.x1,
        "ax_top": 1.0 - pos.y1,  # fractions from the image top
        "ax_bottom": 1.0 - pos.y0,
        "lon_min": float(lon.min()),
        "lon_max": float(lon.max()),
        "lat_min": float(lat.min()),
        "lat_max": float(lat.max()),
    }


def render_frame(out, S, lon, lat, box, z500_dam, t850, u850, v850):
    lon2d, lat2d = np.meshgrid(lon, lat)
    fig, ax = make_figure(S, lon, lat)
    pc = ccrs.PlateCarree()
    cmap = plt.get_cmap("Spectral_r")
    cf = ax.contourf(lon2d, lat2d, z500_dam, levels=np.arange(500, 601, 5), cmap=cmap,
                     extend="both", transform=pc, zorder=101)  # fmt: skip
    lc = ax.contour(lon2d, lat2d, t850, levels=np.arange(250, 301, 5), colors="b",
                    linewidths=1.5, transform=pc, zorder=103)  # fmt: skip
    ax.clabel(lc, colors=["b"], inline=True, fmt=" {:.0f} ".format, fontsize=8, zorder=103)
    skip = 10
    ax.barbs(lon2d[::skip, ::skip], lat2d[::skip, ::skip], u850[::skip, ::skip],
             v850[::skip, ::skip], length=5, linewidth=0.6, transform=pc, zorder=104)  # fmt: skip
    ax.coastlines(linewidth=0.6, zorder=102, resolution="50m")
    gl = ax.gridlines(crs=pc, xlocs=range(-180, 181, 10), ylocs=range(-80, 81, 10),
                      draw_labels=True, zorder=102, linewidth=0.2, color="k", linestyle="--")  # fmt: skip
    gl.top_labels = False
    gl.right_labels = False
    ax.add_patch(Rectangle((box[0], box[2]), box[1] - box[0], box[3] - box[2], fill=False,
                           edgecolor="k", linestyle="--", linewidth=2.2, transform=pc, zorder=106))  # fmt: skip
    cax = fig.add_axes([0.15, 0.045, 0.72, 0.018])
    cb = fig.colorbar(cf, cax=cax, orientation="horizontal")
    cb.set_label("Z500 (dam)   ·   blue contours: T850 (K)   ·   barbs: 850 hPa wind")
    buf = io.BytesIO()
    fig.savefig(buf, dpi=S["build"]["dpi"], format="png")
    plt.close(fig)
    # 128-colour palette PNG: visually identical, ~3.5x smaller
    img = Image.open(buf).convert("RGB")
    img.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(
        out, optimize=True
    )


def render_overlay(out, S, lon, lat, masks):
    lon2d, lat2d = np.meshgrid(lon, lat)
    fig, ax = make_figure(S, lon, lat)
    ax.set_facecolor("none")
    fig.patch.set_alpha(0.0)
    ax.spines["geo"].set_visible(False)
    pc = ccrs.PlateCarree()
    with plt.rc_context({"hatch.color": "red", "hatch.linewidth": 1.5}):
        for m in masks:
            m = m.astype(float)
            ax.contourf(lon2d, lat2d, m, levels=[0.5, 1.5], colors="none", hatches=["///"],
                        transform=pc, zorder=105)  # fmt: skip
            ax.contour(lon2d, lat2d, m, levels=[0.5], colors="red", linewidths=2.0,
                       transform=pc, zorder=105)  # fmt: skip
    fig.savefig(out, dpi=S["build"]["dpi"], transparent=True)
    plt.close(fig)


def build_year_cases(year, path, cases, scan, params, S, cases_dir, no_loop):
    get_logger("expert_eval", level=logging.INFO)
    B = S["build"]
    dt_hours = scan["dt_hours"]
    nloop = 0 if no_loop else int(round(B["loop_hours"] / dt_hours))
    frames_dir = cases_dir / "frames"
    eval_dir = cases_dir / "eval"
    fields_dir = eval_dir / "fields"
    overlay_dir = cases_dir / "overlays"
    box = params.col_region
    out = {}
    with xr.open_dataset(path) as ds:
        times = pd.DatetimeIndex(ds.time.values)
        lat, lon, lat2d, lon2d = open_grid(ds)
        ny, nx = lat.size, lon.size
        area2d, _ = grid_spacing(lat2d, lon2d)
        for c in cases:
            t = c["t"]
            cy = ds.cy_z500_objects.isel(time=t).values.astype(np.int32)
            col = ds.col_objects.isel(time=t).values.astype(np.int32)
            # --- frames
            frame_ids = []
            for k in range(t - nloop, t + nloop + 1):
                fid = times[k].strftime("%Y%m%d%H")
                frame_ids.append(fid)
                fpath = frames_dir / f"{fid}.png"
                if fpath.exists():
                    continue
                sub = ds.isel(time=k)
                render_frame(
                    fpath, S, lon, lat, box,
                    sub.z500.values / const.g / 10.0,
                    sub.t850.values, sub.u850.values, sub.v850.values,
                )  # fmt: skip
            # --- evaluation data
            col_masks = []
            cols = []
            for cid in np.unique(col)[1:].tolist():
                m = col == cid
                col_masks.append(m)
                first, last = scan["col_life"].get(cid, (t, t))
                rec = scan["records"].get((t, cid))
                com = ndimage.center_of_mass(m)
                ci, cj = int(round(com[0])), int(round(com[1]))
                zi, zj = rec["zmin_ij"] if rec else (ci, cj)
                cols.append(
                    {
                        "id": cid,
                        "mask": encode_mask(m),
                        "zmin_lat": float(lat[zi]),
                        "zmin_lon": float(lon[zj]),
                        "centroid_lat": float(lat[ci]),
                        "centroid_lon": float(lon[cj]),
                        "n_cells": int(m.sum()),
                        "area_km2": round(float(area2d[m].sum()) / 1e6),
                        "life_hours": (last - first + 1) * dt_hours,
                        "hours_since_onset": (t - first) * dt_hours,
                        "hours_to_decay": (last - t) * dt_hours,
                    }
                )
            cyclones = []
            for oid in np.unique(cy)[1:].tolist():
                rec = scan["records"].get((t, oid))
                if rec is None:
                    continue
                life = scan["col_life"].get(oid)
                cyclones.append(
                    {
                        "id": oid,
                        "mask": encode_mask(cy == oid),
                        "is_col": oid in [x["id"] for x in cols],
                        "zmin_lat": rec["zmin_lat"],
                        "zmin_lon": rec["zmin_lon"],
                        "col_life": (
                            {
                                "first": times[life[0]].isoformat(),
                                "last": times[life[1]].isoformat(),
                            }
                            if life
                            else None
                        ),  # fmt: skip
                        "record": {k: v for k, v in rec.items() if k not in ("t",)},
                    }
                )
            ev = {
                "case_id": c["case_id"],
                "time": c["time"],
                "year": year,
                "dt_hours": dt_hours,
                "category": c["category"],
                "tags": c["tags"],
                "events": c["events"],
                "high_impact_event": c.get("high_impact_event"),
                "algo_n_cols": len(cols),
                "box": list(box),
                "grid": {
                    "lat0": float(lat[0]),
                    "dlat": float(lat[1] - lat[0]),
                    "nlat": ny,
                    "lon0": float(lon[0]),
                    "dlon": float(lon[1] - lon[0]),
                    "nlon": nx,
                },
                "cols": cols,
                "cyclones": cyclones,
            }
            (eval_dir / f"{c['case_id']}.json").write_text(json.dumps(ev))
            z_m = ds.z500.isel(time=t).values / const.g
            np.savez_compressed(
                fields_dir / f"{c['case_id']}.npz",
                z500_m_minus_5500=(z_m - 5500.0).astype(np.float16),
                u200=ds.u200.isel(time=t).values.astype(np.float16),
                lat=lat.astype(np.float32),
                lon=lon.astype(np.float32),
            )
            render_overlay(overlay_dir / f"{c['case_id']}.png", S, lon, lat, col_masks)
            out[c["case_id"]] = {
                "month": c["month"],
                "frames": frame_ids,
                "center_index": nloop,
                "dt_hours": dt_hours,
            }
    return out


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--years", type=int, nargs="*", help="years to use (default: all finished files)"
    )
    ap.add_argument("--n-cases", type=int, default=None, help="number of NEW cases to add")
    ap.add_argument(
        "--target-total",
        type=int,
        default=None,
        help="add cases until the pool has this many, keeping the category proportions",
    )
    ap.add_argument(
        "--high-impact", default=None, help="TOML with high-impact events (one case each)"
    )
    ap.add_argument(
        "--min-gap-same-event",
        type=float,
        default=None,
        help="override [build] min_gap_hours_same_event for this run",
    )
    ap.add_argument(
        "--repeat-events",
        action="store_true",
        help="allow more than one case per DANA/cyclone event and category (still >= min gap apart)",
    )
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-loop", action="store_true", help="render only the central frame")
    ap.add_argument("--force", action="store_true", help="also use files modified recently")
    ap.add_argument("--scan-only", action="store_true", help="scan and print pool statistics")
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="recompute algorithm data for all existing cases (after tuning); adds no cases",
    )
    args = ap.parse_args()

    S = load_settings()
    B = S["build"]
    if args.min_gap_same_event is not None:
        B["min_gap_hours_same_event"] = args.min_gap_same_event
    n_new = args.n_cases if args.n_cases is not None else B["n_cases"]
    cases_dir = S["paths"]["cases_dir"]
    for sub in ("frames", "overlays", "eval/fields", ".scan"):
        (cases_dir / sub).mkdir(parents=True, exist_ok=True)

    params = ColParams.from_cfg(cfg, g=const.g)
    files = tracking_files(set(args.years) if args.years else None, force=args.force)
    if not files:
        sys.exit("No tracking files found")
    logger.info(f"Scanning {len(files)} years with {args.jobs} jobs")
    scans = Parallel(n_jobs=args.jobs)(
        delayed(scan_year)(y, p, params, S, cases_dir / ".scan") for y, p in files.items()
    )
    scans = {s["year"]: s for s in scans}

    n_rec = sum(s["n_records"] for s in scans.values())
    n_mis = sum(s["n_mismatch"] for s in scans.values())
    logger.info(f"Criteria re-evaluation vs saved col_objects: {n_mis}/{n_rec} records differ")
    candidates = [c for s in scans.values() for c in s["candidates"]]
    counts = defaultdict(int)
    tag_counts = defaultdict(int)
    for c in candidates:
        counts[c["category"]] += 1
        for tg in c["tags"]:
            tag_counts[(c["category"], tg)] += 1
    for k in sorted(counts):
        logger.info(f"  pool {k:17s}: {counts[k]}")
    for (cat, tg), n in sorted(tag_counts.items()):
        logger.info(f"      {cat:17s} {tg:25s}: {n}")
    if args.scan_only:
        return

    manifest_path = cases_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"cases": {}}
    existing = []
    for cid in manifest["cases"]:
        evp = cases_dir / "eval" / f"{cid}.json"
        if evp.exists():
            e = json.loads(evp.read_text())
            existing.append({"case_id": cid, "time": e["time"], "category": e["category"],
                             "events": e.get("events", [])})  # fmt: skip

    rng = random.Random(args.seed)
    by_id = {c["case_id"]: c for c in candidates}
    version = algo_version(params, files)
    if (
        manifest["cases"]
        and manifest.get("algo_version") not in (None, version)
        and not args.refresh
    ):
        logger.warning(
            "Tracking output, thresholds or code changed since the last build "
            f"({manifest.get('algo_version')} -> {version}): existing cases still hold the old "
            "algorithm answers. Run with --refresh (see README, 'After tuning the algorithm')."
        )

    if args.refresh:
        chosen, changes = [], defaultdict(int)
        for cid in sorted(manifest["cases"]):
            c = by_id.get(cid)
            if c is None:
                logger.warning(
                    f"  {cid}: no longer a candidate time (year missing or at a year edge); kept as is"
                )
                continue
            c = dict(c)
            hi = manifest.get("high_impact", {}).get(cid)
            if hi:
                c["tags"] = sorted(set(c["tags"]) | {"high_impact"})
                c["high_impact_event"] = hi["event"]
            old = next((e for e in existing if e["case_id"] == cid), None)
            if old and old["category"] != c["category"]:
                changes[(old["category"], c["category"])] += 1
            chosen.append(c)
        logger.info(f"Refreshing {len(chosen)} cases for algorithm version {version}")
        for (a, b), n in sorted(changes.items()):
            logger.info(f"  category {a:17s} -> {b:17s}: {n}")
        if not changes:
            logger.info("  no category changes")

    # ---- high-impact events: forced cases (existing ones are only tagged)
    forced = []
    if args.high_impact and not args.refresh:
        dt_hours = next(iter(scans.values()))["dt_hours"]
        events = load_high_impact(args.high_impact, dt_hours, B["loop_hours"])
        manifest["high_impact"] = {}
        for ev in events:
            cid = ev["case_id"]
            manifest["high_impact"][cid] = {"event": ev["id"], "maps": ev["maps"]}
            evp = cases_dir / "eval" / f"{cid}.json"
            if cid in manifest["cases"] and evp.exists():
                e = json.loads(evp.read_text())
                e["tags"] = sorted(set(e["tags"]) | {"high_impact"})
                e["high_impact_event"] = ev["id"]
                evp.write_text(json.dumps(e))
                logger.info(
                    f"  high-impact {ev['id']:10s} {cid} already in the pool ({e['category']}): tagged"
                )
                continue
            c = by_id.get(cid)
            if c is None:
                logger.warning(
                    f"  high-impact {ev['id']}: {cid} not available (outside scanned data or year edge)"
                )
                continue
            c = {
                **c,
                "tags": sorted(set(c["tags"]) | {"high_impact"}),
                "high_impact_event": ev["id"],
            }
            forced.append(c)
            logger.info(
                f"  high-impact {ev['id']:10s} {cid} maps={','.join(ev['maps'])} -> {c['category']}"
            )
        for c in forced:
            existing.append(c)

    # ---- quotas: fixed number of new cases, or fill up to a target total
    if args.refresh:
        pass
    elif args.target_total is not None:
        target = quotas_for(args.target_total, B)
        have = defaultdict(int)
        for c in existing:
            have[c["category"]] += 1
        quotas = {cat: max(0, target[cat] - have[cat]) for cat in CATEGORIES}
        logger.info(f"Target {args.target_total}: have {dict(have)} -> adding {quotas}")
    else:
        quotas = quotas_for(n_new, B)
        logger.info(f"Selecting {n_new} new cases ({len(existing)} already in the pool)")
    if not args.refresh:
        chosen = forced + select_cases(candidates, quotas, B, existing, rng, args.repeat_events)

    by_year = defaultdict(list)
    for c in chosen:
        by_year[c["year"]].append(c)
    any_file = next(iter(files.values()))
    with xr.open_dataset(any_file) as ds:
        lat, lon, _, _ = open_grid(ds)
    geometry = map_geometry(S, lon, lat)
    if manifest.get("geometry") and manifest["geometry"] != geometry:
        logger.warning("Map geometry differs from the existing manifest; old frames may misalign")

    logger.info(f"Rendering {len(chosen)} cases over {len(by_year)} years")
    results = Parallel(n_jobs=args.jobs)(
        delayed(build_year_cases)(y, files[y], cs, scans[y], params, S, cases_dir, args.no_loop)
        for y, cs in sorted(by_year.items())
    )
    for r in results:
        manifest["cases"].update(r)

    manifest.update(
        {
            "version": 1,
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "git_hash": git_hash(),
            "col_params": params.asdict(),
            "algo_version": version,
            "box": list(params.col_region),
            "loop_hours": B["loop_hours"],
            "geometry": geometry,
        }
    )
    manifest.setdefault("builds", []).append(
        {
            "at": manifest["updated_at"],
            "git_hash": manifest["git_hash"],
            "algo_version": version,
            "refresh": bool(args.refresh),
            "col_params": params.asdict(),
            "cases": sorted(c["case_id"] for c in chosen),
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=1))
    logger.info(f"Done: {len(manifest['cases'])} cases in {manifest_path}")


if __name__ == "__main__":
    main()
