#!/usr/bin/env python
"""Tropical Cyclone (TC) tracking filter.

Takes pre-computed SLP cyclone objects (from CY_ACY_slp_tracking) and
applies a sequential 12-step filter to identify confirmed tropical cyclones:
genesis latitude, warm-core temperature, duration, SLP threshold, and
land/water genesis checks.
"""

import logging
import time
from itertools import groupby

import numpy as np
from scipy.ndimage import find_objects, gaussian_filter

from atmotrack import config as cfg
from atmotrack.grid import Grid, to_pm180
from atmotrack.output import write_tracking_file
from atmotrack.utils import Fore, Style

logger = logging.getLogger("atmotrack")

# ---------------------------------------------------------------------------
# Land geometry — lazy singleton; cartopy/shapely loaded on first call to
# is_land() so that the module can be imported without those packages.
# ---------------------------------------------------------------------------
_land = None


def _get_land():
    """Return (and lazily initialise) the prepared land geometry."""
    global _land
    if _land is None:
        import cartopy.io.shapereader as shpreader
        import shapely.geometry as sgeom  # noqa: F401  (needed by is_land caller)
        from shapely.ops import unary_union
        from shapely.prepared import prep

        _land_shp = shpreader.natural_earth(resolution="50m", category="physical", name="land")
        _land_geom = unary_union(list(shpreader.Reader(_land_shp).geometries()))
        _land = prep(_land_geom)
    return _land


def is_land(lon, lat):
    """Return True if the point (lon, lat) is over land."""
    import shapely.geometry as sgeom

    return _get_land().contains(sgeom.Point(float(to_pm180(lon)), lat))


# ---------------------------------------------------------------------------
# TC_tracking
# ---------------------------------------------------------------------------


def TC_tracking(CY_objects, t850, slp, Lon, Lat, times=None, nc_file=None):
    """Filter SLP cyclone objects into confirmed tropical cyclones.

    Parameters
    ----------
    CY_objects : ndarray, shape (time, lat, lon), int
        Labelled cyclone objects from CY_ACY_slp_tracking.
    t850 : ndarray, shape (time, lat, lon), float
        850 hPa temperature [K].
    slp : ndarray, shape (time, lat, lon), float
        Mean sea-level pressure [Pa].
    Lon : ndarray, shape (lat, lon)
        2-D longitude grid [°].
    Lat : ndarray, shape (lat, lon)
        2-D latitude grid [°].
    times : DatetimeIndex or None
        Timestamps for the time axis (used in NC output).
    nc_file : str or None
        If given, write ``tc_objects`` and ``slp`` to this NetCDF path.

    Returns
    -------
    TC_obj : ndarray, shape (time, lat, lon), int
        Labelled TC objects; 0 = background; labels equal original CY IDs.
    TC_Tracks : dict
        Keys are str(label); values are (n_timesteps, 2) arrays of
        (lat, lon) per timestep with NaN where TC criteria fail.
    """
    start_time = time.time()

    TC_lat_genesis = cfg.TC_lat_genesis
    TC_deltaT_core = cfg.TC_deltaT_core
    TC_T850min = cfg.TC_T850min
    TC_Pmin = cfg.TC_Pmin
    TC_lat_max = cfg.TC_lat_max
    DT = cfg.DT
    # Durations are configured in hours and converted to time steps here
    TC_min_duration = int(cfg.get("TC_min_duration_h", renamed_from="TC_min_duration") / DT)
    TC_persistence = int(cfg.get("TC_persistence_h", default=24) / DT)

    grid = Grid(Lon, Lat)
    periodic = grid.is_global_periodic

    TC_Tracks = {}
    TC_obj = np.zeros_like(CY_objects, dtype=int)

    Objects = find_objects(CY_objects.astype(int))

    for ii, obj_slice in enumerate(Objects):
        if obj_slice is None:
            continue

        ObjACT = CY_objects[obj_slice] == (ii + 1)

        # Step 2 — minimum duration filter
        if ObjACT.shape[0] < TC_min_duration:
            continue

        T_ACT = np.copy(t850[obj_slice])
        slp_ACT = np.copy(slp[obj_slice]) / 100.0  # Pa → hPa

        LonObj = Lon[obj_slice[1], obj_slice[2]]
        LatObj = Lat[obj_slice[1], obj_slice[2]]

        # Step 3 — objects wrapping around the longitude seam of a global grid
        date_line = periodic and bool(ObjACT[:, :, 0].any() and ObjACT[:, :, -1].any())
        if date_line:
            roll_amount = int(ObjACT.shape[2] / 2)
            ObjACT = np.roll(ObjACT, roll_amount, axis=2)
            slp_ACT = np.roll(slp_ACT, roll_amount, axis=2)

        # Step 4 — find SLP minimum centre per timestep
        slp_ACT[ObjACT == 0] = 999999999.0
        Track_ACT = np.array(
            [
                np.argwhere(slp_ACT[tt, :, :] == np.nanmin(slp_ACT[tt, :, :]))[0]
                for tt in range(ObjACT.shape[0])
            ]
        )

        # Step 5 — build lat/lon track
        LatLonTrackAct = np.array(
            [
                (
                    LatObj[Track_ACT[tt, 0], Track_ACT[tt, 1]],
                    LonObj[Track_ACT[tt, 0], Track_ACT[tt, 1]],
                )
                for tt in range(ObjACT.shape[0])
            ],
            dtype=float,
        )

        # Step 6 — genesis latitude filter
        if np.min(np.abs(LatLonTrackAct[:, 0])) > TC_lat_genesis:
            continue

        # Step 7 — warm-core ΔT
        DeltaTCore = np.full(ObjACT.shape[0], np.nan)
        T850_core = np.full(ObjACT.shape[0], np.nan)
        for tt in range(ObjACT.shape[0]):
            r0 = max(Track_ACT[tt, 0] - 1, 0)
            r1 = min(Track_ACT[tt, 0] + 2, T_ACT.shape[1])
            c0 = max(Track_ACT[tt, 1] - 1, 0)
            c1 = min(Track_ACT[tt, 1] + 2, T_ACT.shape[2])
            T_cent = np.mean(T_ACT[tt, r0:r1, c0:c1])
            T850_core[tt] = T_cent
            obj_mask = ObjACT[tt, :, :] != 0
            if obj_mask.any():
                T_Cyclone = np.mean(T_ACT[tt, obj_mask])
                DeltaTCore[tt] = T_cent - T_Cyclone

        DeltaTCore = gaussian_filter(DeltaTCore, sigma=1)
        WarmCore = DeltaTCore > TC_deltaT_core

        # Step 8 — warm-core duration
        if np.sum(WarmCore) < TC_persistence:
            continue
        ObjACT[~WarmCore, :, :] = 0

        # Step 9 — core temperature
        ObjACT[T850_core < TC_T850min, :, :] = 0

        # Step 10 — minimum SLP
        MinPress = np.min(slp_ACT, axis=(1, 2))
        if np.sum(MinPress < TC_Pmin) < TC_persistence:
            continue

        # Step 11 — TCcheck mask: combine all three criteria
        TCcheck = (T850_core > TC_T850min) & WarmCore & (MinPress < TC_Pmin)
        LatLonTrackAct[~TCcheck, :] = np.nan

        # Step 12 — max latitude cutoff
        max_lat_mask = np.abs(LatLonTrackAct[:, 0]) > TC_lat_max
        LatLonTrackAct[max_lat_mask, :] = np.nan

        # Step 13 — skip if all NaN after filters
        if np.all(np.isnan(LatLonTrackAct[:, 0])):
            continue

        # Step 14 — land/water genesis check per continuous segment
        resultLAT = [
            list(map(float, g)) for k, g in groupby(LatLonTrackAct[:, 0], np.isnan) if not k
        ]
        resultLON = [
            list(map(float, g)) for k, g in groupby(LatLonTrackAct[:, 1], np.isnan) if not k
        ]
        LS_genesis = np.full(len(resultLAT), np.nan)
        for jj in range(len(resultLAT)):
            LS_genesis[jj] = float(is_land(resultLON[jj][0], resultLAT[jj][0]))

        if np.nanmax(LS_genesis) == 1:
            for jj in range(len(LS_genesis)):
                if LS_genesis[jj] == 1:
                    land_lats = np.array(resultLAT[jj])
                    set_nan = np.isin(LatLonTrackAct[:, 0], land_lats)
                    LatLonTrackAct[set_nan, :] = np.nan

        # Step 15 — zero out non-TC timesteps; undo date-line roll
        ObjACT[np.isnan(LatLonTrackAct[:, 0]), :, :] = 0

        if date_line:
            ObjACT = np.roll(ObjACT, -roll_amount, axis=2)

        # Step 16 — write relabelled slice into TC_obj; store track
        ObjACT = ObjACT.astype(int)
        ObjACT[ObjACT != 0] = ii + 1
        TC_obj[obj_slice] = TC_obj[obj_slice] + ObjACT
        TC_Tracks[str(ii + 1)] = LatLonTrackAct

    end_time = time.time()
    logger.debug(f"{Fore.CYAN}======> TC tracking completed in {end_time - start_time:.2f} seconds")
    logger.info(f"{Style.BRIGHT}TC objects found: {TC_obj.max()}, tracks stored: {len(TC_Tracks)}")

    # -----------------------------------------------------------------------
    # NetCDF output
    # -----------------------------------------------------------------------
    if nc_file is not None:
        logger.debug(f"{Style.BRIGHT} Save TC objects into a NetCDF")

        if times is None:
            raise ValueError("TC_tracking needs `times` to write a NetCDF file")
        write_tracking_file(
            nc_file,
            times,
            grid,
            {
                "tc_objects": (TC_obj, {"long_name": "tropical cyclone object labels"}),
                "slp": (slp, {"units": "Pa", "long_name": "mean sea-level pressure"}),
            },
        )

        logger.debug(f"{Style.BRIGHT} TC NetCDF written to {nc_file}")
    else:
        logger.debug(f"{Fore.YELLOW}No output file requested (nc_file=None)")

    return TC_obj, TC_Tracks
