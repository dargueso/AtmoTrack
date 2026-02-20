#!/usr/bin/env python
"""
test_smoke.py — AtmoTrack end-to-end smoke tests.

Uses 7 days of synthetic ERA5-like data (28 × 6-hourly steps, 0.5°
resolution) to verify that the three main tracking functions run without
error and return outputs with the correct shape.

The synthetic fields include a deep moving trough / surface low centred in
the COL region (30–45°N, −15–10°E) so that the trackers can actually detect
objects, making any regression in the detection logic immediately visible.

Run with pytest (recommended):
    pytest test_smoke.py -v

Or run directly as a plain Python script:
    python test_smoke.py
"""

import sys

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Synthetic ERA5-like grid  (0.5° resolution, same domain as download_ERA5.py)
# ---------------------------------------------------------------------------
_LAT = np.arange(75.0, 9.75, -0.5)  # 75 → 10 °N  → 131 points
_LON = np.arange(-30.0, 20.25, 0.5)  # −30 → 20 °E → 101 points
_LON2D, _LAT2D = np.meshgrid(_LON, _LAT)

# 7 days × 4 timesteps/day = 28 steps at 6-hourly frequency
# Arbitrary start date — the tracking functions only use the number of steps,
# not the actual calendar values.
_TIMES = pd.date_range("2000-01-01 00:00", periods=28, freq="6h")
_NT, _NLAT, _NLON = len(_TIMES), len(_LAT), len(_LON)

_RNG = np.random.default_rng(42)


# ---------------------------------------------------------------------------
# Synthetic field factories
# ---------------------------------------------------------------------------


def _z500():
    """Geopotential at 500 hPa [m²/s²].

    Background ≈ 53 000 m²/s² (≈ 5 400 m) with a deep trough (≈ 255 m deep
    in geopotential height) that drifts eastward across the COL region,
    guaranteeing anomalies well below the −80 m (= −785 m²/s²) threshold.
    """
    z = np.empty((_NT, _NLAT, _NLON), dtype=np.float64)
    for t in range(_NT):
        lon_c = -12.0 + t * 0.4  # trough drifts east from −12° to −1°
        dist2 = (_LON2D - lon_c) ** 2 + (_LAT2D - 40.0) ** 2
        trough = -2_500.0 * np.exp(-dist2 / 20.0)  # ≈ 255 m deep
        wave = 500.0 * np.sin(2 * np.pi * _LON2D / 8.0)
        z[t] = 53_000.0 + trough + wave
    return z


def _slp():
    """Mean sea-level pressure [Pa].

    Background 101 325 Pa (1 013.25 hPa) with a surface low (~20 hPa deep)
    that drifts eastward, exceeding the −10 hPa (= −1 000 Pa) cyclone
    threshold.
    """
    slp = np.empty((_NT, _NLAT, _NLON), dtype=np.float64)
    for t in range(_NT):
        lon_c = -8.0 + t * 0.3
        dist2 = (_LON2D - lon_c) ** 2 + (_LAT2D - 42.0) ** 2
        low = -2_000.0 * np.exp(-dist2 / 15.0)  # ~20 hPa deep
        slp[t] = 101_325.0 + low
    return slp


def _wind(scale=10.0):
    """Synthetic wind component [m/s]."""
    return _RNG.normal(0, scale, size=(_NT, _NLAT, _NLON))


def _temp(base=270.0):
    """Synthetic temperature [K]."""
    return np.full((_NT, _NLAT, _NLON), base) + _RNG.normal(0, 5, size=(_NT, _NLAT, _NLON))


def _precip():
    """Synthetic 6-hourly precipitation [mm], non-negative."""
    return np.abs(_RNG.normal(0, 2, size=(_NT, _NLAT, _NLON)))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_imports():
    """All AtmoTrack modules import without error and constants are correct."""
    import atmotrack_config as cfg
    import constants
    import tracking_functions  # noqa: F401
    import utils  # noqa: F401

    assert abs(constants.const.g - 9.81) < 1e-9, "Gravity constant wrong"
    assert constants.const.earth_radius == 6_371_000, "Earth radius wrong"
    assert hasattr(cfg, "DT"), "cfg.DT missing"
    assert hasattr(cfg, "col_min_dur"), "cfg.col_min_dur missing"
    print("PASS  imports")


def test_cy_z500_tracking():
    """CY_ACY_z500_tracking returns two arrays of the correct shape."""
    from tracking_functions import CY_ACY_z500_tracking

    z500 = _z500()
    cy, acy = CY_ACY_z500_tracking(z500, _TIMES, _LON2D, _LAT2D, nc_file=None)

    assert cy.shape == (_NT, _NLAT, _NLON), f"cy shape: {cy.shape}"
    assert acy.shape == (_NT, _NLAT, _NLON), f"acy shape: {acy.shape}"
    assert cy.min() >= 0, "cy_objects contains negative IDs"
    assert acy.min() >= 0, "acy_objects contains negative IDs"

    print(f"PASS  cy_z500_tracking  (cy labels: {cy.max()},  acy labels: {acy.max()})")


def test_slp_tracking():
    """CY_ACY_slp_tracking returns two arrays of the correct shape."""
    from tracking_functions import CY_ACY_slp_tracking

    slp = _slp()
    cy, acy = CY_ACY_slp_tracking(slp, _TIMES, _LON2D, _LAT2D, nc_file=None)

    assert cy.shape == (_NT, _NLAT, _NLON), f"cy_slp shape: {cy.shape}"
    assert acy.shape == (_NT, _NLAT, _NLON), f"acy_slp shape: {acy.shape}"
    assert cy.min() >= 0, "cy_slp_objects contains negative IDs"
    assert acy.min() >= 0, "acy_slp_objects contains negative IDs"

    print(f"PASS  slp_tracking  (cy labels: {cy.max()},  acy labels: {acy.max()})")


def test_col_tracking():
    """COL_tracking returns an array of the correct shape.

    Runs CY_ACY_z500_tracking first to obtain the cy_z500_objects input
    required by COL_tracking, mirroring the real pipeline in
    COL_tracking_ERA5.py.
    """
    from tracking_functions import COL_tracking, CY_ACY_z500_tracking

    z500 = _z500()
    cy, _ = CY_ACY_z500_tracking(z500, _TIMES, _LON2D, _LAT2D, nc_file=None)

    pr = _precip()
    col = COL_tracking(
        cy,
        z500,
        _wind(20.0),  # u200
        _wind(10.0),  # u850
        _wind(10.0),  # v850
        _temp(270.0),  # t850
        pr,  # pr_data    (6-hourly sum)
        pr,  # pr_data_max (6-hourly max; same array is fine for a smoke test)
        times=_TIMES,
        Lon=_LON2D,
        Lat=_LAT2D,
        nc_file=None,
    )

    assert col.shape == (_NT, _NLAT, _NLON), f"col shape: {col.shape}"
    assert col.min() >= 0, "col_objects contains negative IDs"

    print(f"PASS  col_tracking  (col labels: {col.max()})")


# ---------------------------------------------------------------------------
# Standalone runner (no pytest required)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Try to run under pytest first; fall back to plain Python assertions.
    try:
        import pytest

        sys.exit(pytest.main([__file__, "-v"] + sys.argv[1:]))
    except ImportError:
        pass

    tests = [test_imports, test_cy_z500_tracking, test_slp_tracking, test_col_tracking]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as exc:
            print(f"FAIL  {fn.__name__}: {exc}")
            failed += 1
    if failed == 0:
        print(f"\nAll {len(tests)} smoke tests passed.")
    else:
        print(f"\n{failed}/{len(tests)} smoke tests FAILED.")
    sys.exit(failed)
