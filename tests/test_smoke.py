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
# Synthetic ERA5-like grid  (0.5° resolution, same domain as atmotrack-download-era5)
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
    import atmotrack.tracking  # noqa: F401
    from atmotrack import config as cfg
    from atmotrack import (
        constants,
        utils,  # noqa: F401
    )

    assert abs(constants.const.g - 9.81) < 1e-9, "Gravity constant wrong"
    assert constants.const.earth_radius == 6_371_000, "Earth radius wrong"
    assert hasattr(cfg, "DT"), "cfg.DT missing"
    assert hasattr(cfg, "col_min_dur"), "cfg.col_min_dur missing"
    print("PASS  imports")


def test_cy_z500_tracking():
    """CY_ACY_z500_tracking returns two arrays of the correct shape."""
    from atmotrack.tracking import CY_ACY_z500_tracking

    z500 = _z500()
    cy, acy = CY_ACY_z500_tracking(z500, _TIMES, _LON2D, _LAT2D, nc_file=None)

    assert cy.shape == (_NT, _NLAT, _NLON), f"cy shape: {cy.shape}"
    assert acy.shape == (_NT, _NLAT, _NLON), f"acy shape: {acy.shape}"
    assert cy.min() >= 0, "cy_objects contains negative IDs"
    assert acy.min() >= 0, "acy_objects contains negative IDs"

    print(f"PASS  cy_z500_tracking  (cy labels: {cy.max()},  acy labels: {acy.max()})")


def test_slp_tracking():
    """CY_ACY_slp_tracking returns two arrays of the correct shape."""
    from atmotrack.tracking import CY_ACY_slp_tracking

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
    atmotrack-col.
    """
    from atmotrack.tracking import COL_tracking, CY_ACY_z500_tracking

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


def test_front_tracking():
    """Front_tracking returns an array of the correct shape."""
    from atmotrack.tracking import Front_tracking

    fr = Front_tracking(
        _wind(10.0),  # u850
        _wind(10.0),  # v850
        _temp(270.0),  # t850
        _TIMES,
        _LON2D,
        _LAT2D,
    )

    assert fr.shape == (_NT, _NLAT, _NLON), f"front shape: {fr.shape}"
    assert fr.min() >= 0, "front_objects contains negative IDs"
    print(f"PASS  front_tracking  (front labels: {fr.max()})")


def test_mcs_tracking():
    """MCS_tracking returns a dict and an array of the correct shape.

    Uses high BT values (280 K, well above the 241 K cloud-shield threshold)
    and low precipitation so that no objects are detected — this keeps the
    test fast and avoids needing cfg.path_in to exist on disk.
    """
    from atmotrack.tracking import MCS_tracking

    pr = _precip()  # low values → no PR objects above threshold
    bt = np.full((_NT, _NLAT, _NLON), 280.0)  # all > 241 K → no cloud shield

    grMCSs, mcs_objs = MCS_tracking(pr, bt, _TIMES, _LON2D, _LAT2D, nc_file=None)

    assert isinstance(grMCSs, dict), f"grMCSs should be dict, got {type(grMCSs)}"
    assert mcs_objs.shape == (_NT, _NLAT, _NLON), f"mcs_objs shape: {mcs_objs.shape}"
    assert mcs_objs.min() >= 0, "mcs_objects contains negative IDs"
    print(f"PASS  mcs_tracking  (mcs labels: {mcs_objs.max()})")


def test_tc_tracking():
    """TC_tracking returns an object array and track dict of correct shape/type.

    Runs CY_ACY_slp_tracking on synthetic SLP to get cy_slp_objects, then
    feeds those into TC_tracking with synthetic T850.  The synthetic low is
    centred at ~42°N, so it fails the genesis-latitude filter (TC_lat_genesis
    = 30°) — no TC labels are expected, but shape and types must be correct.
    """
    from atmotrack.tracking import CY_ACY_slp_tracking, TC_tracking

    slp = _slp()
    t850 = _temp(280.0)  # warm enough to pass TC_T850min (273.15 K)
    cy_objs, _ = CY_ACY_slp_tracking(slp, _TIMES, _LON2D, _LAT2D, nc_file=None)

    TC_obj, TC_Tracks = TC_tracking(cy_objs, t850, slp, _LON2D, _LAT2D, nc_file=None)

    assert TC_obj.shape == (_NT, _NLAT, _NLON), f"TC_obj shape: {TC_obj.shape}"
    assert TC_obj.min() >= 0, "TC_obj contains negative IDs"
    assert isinstance(TC_Tracks, dict), f"TC_Tracks should be dict, got {type(TC_Tracks)}"
    print(f"PASS  tc_tracking  (tc labels: {TC_obj.max()}, tracks: {len(TC_Tracks)})")


def test_jetstream_tracking():
    """jetstream_tracking returns an array of the correct shape.

    Uses a synthetic 200 hPa wind field with a strong zonal jet (~50 m/s)
    centred at 45°N so anomaly detection can find objects.
    """
    from atmotrack.tracking import jetstream_tracking

    # Strong zonal jet centred at 45°N — anomaly well above js_min_anomaly (24 m/s)
    uv200 = np.empty((_NT, _NLAT, _NLON), dtype=np.float64)
    for t in range(_NT):
        jet_core = 50.0 * np.exp(-((_LAT2D - 45.0) ** 2) / 10.0)
        uv200[t] = jet_core + _RNG.normal(0, 1, size=(_NLAT, _NLON))

    jet = jetstream_tracking(uv200, _TIMES, _LON2D, _LAT2D, nc_file=None)

    assert jet.shape == (_NT, _NLAT, _NLON), f"jet shape: {jet.shape}"
    assert jet.min() >= 0, "jet_objects contains negative IDs"
    print(f"PASS  jetstream_tracking  (jet labels: {jet.max()})")


def test_ar_850hpa_tracking():
    """AR_850hPa_tracking returns an array of the correct shape.

    Uses a synthetic moisture flux field. With the default MinMSthreshold of
    0.13 g/g·m/s the small synthetic values won't produce objects — that's
    fine; we only check shape and type.
    """
    from atmotrack.tracking import AR_850hPa_tracking

    # Moisture flux magnitude well below the 0.13 threshold → 0 objects expected
    VapTrans = np.abs(_RNG.normal(0, 0.01, size=(_NT, _NLAT, _NLON)))

    ar850 = AR_850hPa_tracking(VapTrans, _TIMES, _LON2D, _LAT2D, nc_file=None)

    assert ar850.shape == (_NT, _NLAT, _NLON), f"ar850 shape: {ar850.shape}"
    assert ar850.min() >= 0, "ar850_objects contains negative IDs"
    print(f"PASS  ar_850hpa_tracking  (ar850 labels: {ar850.max()})")


def test_ar_ivt_tracking():
    """AR_IVT_tracking returns an array of the correct shape.

    Uses a synthetic IVT field. With the default IVTthreshold of 500 kg/m/s
    the synthetic values won't produce objects — that's fine.
    """
    from atmotrack.tracking import AR_IVT_tracking

    # IVT well below the 500 threshold → 0 objects expected
    IVT = np.abs(_RNG.normal(0, 50, size=(_NT, _NLAT, _NLON)))

    ar_ivt = AR_IVT_tracking(IVT, _TIMES, _LON2D, _LAT2D, nc_file=None)

    assert ar_ivt.shape == (_NT, _NLAT, _NLON), f"ar_ivt shape: {ar_ivt.shape}"
    assert ar_ivt.min() >= 0, "ar_ivt_objects contains negative IDs"
    print(f"PASS  ar_ivt_tracking  (ar_ivt labels: {ar_ivt.max()})")


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

    tests = [
        test_imports,
        test_cy_z500_tracking,
        test_slp_tracking,
        test_col_tracking,
        test_front_tracking,
        test_mcs_tracking,
        test_tc_tracking,
        test_jetstream_tracking,
        test_ar_850hpa_tracking,
        test_ar_ivt_tracking,
    ]
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
