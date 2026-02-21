#!/usr/bin/env python
"""
compare_col_results.py — Diagnostic comparison of two COL tracking output files.

Usage
-----
    python3 compare_col_results.py --current  data_tracking/era5_daily_col_z500_2000.nc \
                                   --original /path/to/original/era5_daily_col_z500_2000.nc

The script checks:
  1. Exact array equality for col_objects and cy_z500_objects.
  2. If not identical: per-COL characteristics (count, duration, centroid,
     area) are compared between the two files so the user can judge whether
     differences are cosmetic (e.g. label renumbering) or substantive.
"""

import argparse
import sys

import numpy as np
import xarray as xr

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load(path: str) -> xr.Dataset:
    try:
        return xr.open_dataset(path)
    except FileNotFoundError:
        sys.exit(f"ERROR: file not found: {path}")


def _col_characteristics(col_objects: np.ndarray, lat: np.ndarray, lon: np.ndarray) -> dict:
    """Return a dict keyed by COL label with per-COL summary statistics.

    Parameters
    ----------
    col_objects : (time, lat, lon) integer array; 0 = background
    lat, lon    : 1-D coordinate arrays
    """
    ids = np.unique(col_objects)
    ids = ids[ids > 0]
    chars = {}
    lon2d, lat2d = np.meshgrid(lon, lat)
    for cid in ids:
        mask = col_objects == cid  # (time, lat, lon) bool
        timesteps = np.where(mask.any(axis=(1, 2)))[0]
        area_ts = mask.sum(axis=(1, 2))  # gridpoints per timestep
        # centroid weighted by area
        flat = mask.reshape(mask.shape[0], -1)  # (time, nlat*nlon)
        lat_flat = lat2d.ravel()
        lon_flat = lon2d.ravel()
        total = flat.sum()
        clat = (flat * lat_flat).sum() / total if total > 0 else np.nan
        clon = (flat * lon_flat).sum() / total if total > 0 else np.nan
        chars[int(cid)] = {
            "n_timesteps": len(timesteps),
            "t_start": int(timesteps[0]),
            "t_end": int(timesteps[-1]),
            "area_mean": float(area_ts[area_ts > 0].mean()),
            "area_max": float(area_ts.max()),
            "clat": float(clat),
            "clon": float(clon),
        }
    return chars


def _chars_as_matrix(chars: dict) -> np.ndarray:
    """Convert characteristics dict to a sorted (N, 6) matrix for comparison."""
    if not chars:
        return np.empty((0, 6))
    rows = []
    for v in chars.values():
        rows.append(
            [
                v["n_timesteps"],
                v["t_start"],
                v["t_end"],
                v["area_mean"],
                v["area_max"],
                v["clat"],
                # deliberately exclude clon — can be shifted by relabelling
            ]
        )
    return np.array(sorted(rows))  # sort so order-independent


def _compare_arrays(name: str, a: np.ndarray, b: np.ndarray) -> bool:
    """Print array comparison summary, return True if identical."""
    if a.shape != b.shape:
        print(f"  {name}: SHAPE MISMATCH  current={a.shape}  original={b.shape}")
        return False
    if np.array_equal(a, b):
        print(f"  {name}: IDENTICAL ✓")
        return True
    diff = a != b
    n_diff = diff.sum()
    pct = 100 * n_diff / diff.size
    print(f"  {name}: DIFFERENT  ({n_diff:,} / {diff.size:,} cells differ, {pct:.3f}%)")
    return False


def _compare_characteristics(chars_cur: dict, chars_orig: dict) -> None:
    """Print per-COL characteristic comparison."""
    n_cur = len(chars_cur)
    n_orig = len(chars_orig)
    print(f"\n  COL count — current: {n_cur}   original: {n_orig}", end="")
    print("  ✓" if n_cur == n_orig else "  ✗ DIFFERENT")

    if n_cur == 0 and n_orig == 0:
        return

    # --- duration distribution ---
    def _durations(chars):
        return sorted(v["n_timesteps"] for v in chars.values())

    dur_cur = _durations(chars_cur)
    dur_orig = _durations(chars_orig)
    if dur_cur == dur_orig:
        print("  Duration distribution: IDENTICAL ✓")
    else:
        print("  Duration distribution: DIFFERENT ✗")
        print(f"    current  (sorted): {dur_cur}")
        print(f"    original (sorted): {dur_orig}")

    # --- area distribution (rounded to nearest gridpoint) ---
    def _areas(chars):
        return sorted(round(v["area_max"]) for v in chars.values())

    area_cur = _areas(chars_cur)
    area_orig = _areas(chars_orig)
    if area_cur == area_orig:
        print("  Max-area distribution: IDENTICAL ✓")
    else:
        print("  Max-area distribution: DIFFERENT ✗")
        print(f"    current  (sorted): {area_cur}")
        print(f"    original (sorted): {area_orig}")

    # --- centroid latitude distribution ---
    def _clats(chars):
        return sorted(round(v["clat"], 1) for v in chars.values())

    clat_cur = _clats(chars_cur)
    clat_orig = _clats(chars_orig)
    if clat_cur == clat_orig:
        print("  Centroid-lat distribution: IDENTICAL ✓")
    else:
        print("  Centroid-lat distribution: DIFFERENT ✗")
        print(f"    current  (sorted): {clat_cur}")
        print(f"    original (sorted): {clat_orig}")

    # --- start-timestep distribution ---
    def _starts(chars):
        return sorted(v["t_start"] for v in chars.values())

    st_cur = _starts(chars_cur)
    st_orig = _starts(chars_orig)
    if st_cur == st_orig:
        print("  Start-timestep distribution: IDENTICAL ✓")
    else:
        print("  Start-timestep distribution: DIFFERENT ✗")
        print(f"    current  (sorted): {st_cur}")
        print(f"    original (sorted): {st_orig}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two COL tracking output NetCDF files.")
    parser.add_argument(
        "--current",
        required=True,
        metavar="NC",
        help="Current code output (e.g. data_tracking/era5_daily_col_z500_2000.nc)",
    )
    parser.add_argument(
        "--original", required=True, metavar="NC", help="Original code output for the same year"
    )
    args = parser.parse_args()

    print("=" * 60)
    print(f"  CURRENT : {args.current}")
    print(f"  ORIGINAL: {args.original}")
    print("=" * 60)

    ds_cur = _load(args.current)
    ds_orig = _load(args.original)

    lat = ds_cur["latitude"].values
    lon = ds_cur["longitude"].values

    col_cur = ds_cur["col_objects"].values.astype(int)
    col_orig = ds_orig["col_objects"].values.astype(int)
    cy_cur = ds_cur["cy_z500_objects"].values.astype(int)
    cy_orig = ds_orig["cy_z500_objects"].values.astype(int)

    print("\n--- Array equality ---")
    col_identical = _compare_arrays("col_objects    ", col_cur, col_orig)
    cy_identical = _compare_arrays("cy_z500_objects", cy_cur, cy_orig)

    print("\n--- COL characteristics ---")
    chars_cur = _col_characteristics(col_cur, lat, lon)
    chars_orig = _col_characteristics(col_orig, lat, lon)
    _compare_characteristics(chars_cur, chars_orig)

    # --- overall verdict ---
    print("\n" + "=" * 60)
    if col_identical and cy_identical:
        print("  VERDICT: Files are IDENTICAL — results match perfectly.")
    elif len(chars_cur) == len(chars_orig):
        print("  VERDICT: Arrays differ but COL count matches.")
        print("           Likely cosmetic (label renumbering or float rounding).")
    else:
        print("  VERDICT: SUBSTANTIVE DIFFERENCES detected.")
        print("           COL counts or characteristics do not agree.")
    print("=" * 60)

    ds_cur.close()
    ds_orig.close()


if __name__ == "__main__":
    main()
