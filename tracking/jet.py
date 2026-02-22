#!/usr/bin/env python
"""Jet stream tracking from 200 hPa wind speed."""

import logging
import time

import numpy as np
import xarray as xr
from scipy import ndimage

import atmotrack_config as cfg
from utils import Fore, Style

from .cy_slp import watershed_2d_overlap
from .shared import (
    BreakupObjects,
    ConnectLon_on_timestep,
    calc_grid_distance_area,
    clean_up_objects,
    smooth_uniform,
)

logger = logging.getLogger("atmotrack")


def jetstream_tracking(uv200, times, Lon, Lat, nc_file=None):
    """Track jet stream objects from 200 hPa wind speed.

    Parameters
    ----------
    uv200 : ndarray, shape (time, lat, lon), float
        200 hPa wind speed magnitude [m/s] = sqrt(u² + v²).
    times : DatetimeIndex
        Timestamps corresponding to the first axis of ``uv200``.
    Lon : ndarray, shape (lat, lon)
        2-D longitude grid [°].
    Lat : ndarray, shape (lat, lon)
        2-D latitude grid [°].
    nc_file : str or None
        If given, write ``jet_objects`` to this NetCDF path.

    Returns
    -------
    jet_objects : ndarray, shape (time, lat, lon), int
        Labelled jet objects; 0 = background.
    """
    start_time = time.time()

    DT = cfg.DT
    js_min_anomaly = cfg.js_min_anomaly
    MinTimeJS = cfg.MinTimeJS
    breakup_method = cfg.js_breakup_method

    _, _, _, grid_spacing = calc_grid_distance_area(Lat, Lon)

    crosses_dateline = (Lon[0, 0] < -176) and (Lon[0, -1] > 176)

    obj_structure_3D = np.ones((3, 3, 3))

    end_time = time.time()
    logger.debug(
        f"{Fore.MAGENTA}======> Initialize jet stream tracking: {end_time - start_time:.2f} s"
    )
    start_time = time.time()

    # Smooth wind speed: 500 km spatial, 1 timestep
    uv200_smooth = smooth_uniform(uv200, 1, int(500 / (grid_spacing / 1000.0)))
    # Background: 78 h temporal, 5000 km spatial
    uv200_mean = smooth_uniform(uv200, int(78 / DT), int(5000 / (grid_spacing / 1000.0)))
    uv200_anomaly = uv200_smooth - uv200_mean

    jet = uv200_anomaly >= js_min_anomaly
    objects_id, n_objects = ndimage.label(jet, structure=obj_structure_3D)
    logger.debug(f"{Fore.GREEN} {n_objects} jet stream candidates found")

    jet_objects, _ = clean_up_objects(objects_id, min_tsteps=int(MinTimeJS / DT), dT=DT)

    if breakup_method == "breakup":
        jet_objects = BreakupObjects(jet_objects, int(MinTimeJS / DT), DT)
    elif breakup_method == "watershed":
        min_dist_idx = int(5_000_000 / grid_spacing)
        anom_masked = np.copy(uv200_anomaly)
        anom_masked[jet_objects == 0] = 0
        jet_objects = watershed_2d_overlap(
            anom_masked,
            jet_objects,
            grid_spacing,
            DT,
            int(crosses_dateline),
            min_dist=min_dist_idx,
            threshold=1,
            mintime=MinTimeJS,
        )

    if crosses_dateline:
        jet_objects = ConnectLon_on_timestep(jet_objects)

    end_time = time.time()
    logger.debug(f"{Fore.GREEN}======> Jet stream tracking: {end_time - start_time:.2f} s")
    logger.info(f"{Style.BRIGHT}Jet objects found: {jet_objects.max()}")

    # -----------------------------------------------------------------------
    # NetCDF output
    # -----------------------------------------------------------------------
    if nc_file is not None:
        logger.debug(f"{Style.BRIGHT} Save jet objects into a NetCDF")

        fino = xr.Dataset(
            {
                "jet_objects": (["time", "latitude", "longitude"], jet_objects.astype(np.int16)),
                "uv200": (["time", "latitude", "longitude"], uv200.astype(np.float32)),
            },
            coords={
                "time": times.values,
                "latitude": Lat[:, 0].squeeze(),
                "longitude": Lon[0, :].squeeze(),
            },
        )

        fino.to_netcdf(
            nc_file,
            mode="w",
            encoding={
                "jet_objects": {"zlib": True, "complevel": 5},
                "uv200": {"zlib": True, "complevel": 5},
            },
        )
        logger.debug(f"{Style.BRIGHT} Jet NetCDF written to {nc_file}")
    else:
        logger.debug(f"{Fore.YELLOW}No output file requested (nc_file=None)")

    return jet_objects
