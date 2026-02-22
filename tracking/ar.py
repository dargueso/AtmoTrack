#!/usr/bin/env python
"""Atmospheric River (AR) tracking — 850 hPa moisture flux and IVT approaches."""

import logging
import time

import numpy as np
import xarray as xr
from scipy import ndimage

import atmotrack_config as cfg
from utils import Fore, Style

from .shared import (
    BreakupObjects,
    ConnectLon_on_timestep,
    calc_grid_distance_area,
    clean_up_objects,
)

logger = logging.getLogger("atmotrack")


def AR_850hPa_tracking(VapTrans, times, Lon, Lat, nc_file=None):
    """Track moisture streams / Atmospheric Rivers from 850 hPa moisture flux.

    Parameters
    ----------
    VapTrans : ndarray, shape (time, lat, lon), float
        850 hPa moisture flux magnitude [g/g · m/s] = sqrt((u·q)² + (v·q)²).
    times : DatetimeIndex
        Timestamps corresponding to the first axis of ``VapTrans``.
    Lon : ndarray, shape (lat, lon)
        2-D longitude grid [°].
    Lat : ndarray, shape (lat, lon)
        2-D latitude grid [°].
    nc_file : str or None
        If given, write ``ar850_objects`` to this NetCDF path.

    Returns
    -------
    ar850_objects : ndarray, shape (time, lat, lon), int
        Labelled AR objects; 0 = background.
    """
    start_time = time.time()

    DT = cfg.DT
    MinMSthreshold = cfg.MinMSthreshold
    MinTimeMS = cfg.MinTimeMS
    MinAreaMS = cfg.MinAreaMS  # [km²]

    _, _, grid_cell_area, _ = calc_grid_distance_area(Lat, Lon)
    grid_cell_area[grid_cell_area < 0] = 0

    crosses_dateline = (Lon[0, 0] < -176) and (Lon[0, -1] > 176)

    obj_structure_3D = np.ones((3, 3, 3))

    logger.debug(f"{Style.BRIGHT} Tracking moisture streams from 850 hPa moisture flux")

    potARs = VapTrans > MinMSthreshold
    objects_id, n_objects = ndimage.label(potARs, structure=obj_structure_3D)
    logger.debug(f"{Fore.GREEN} {n_objects} moisture stream candidates found")

    # Area-based filter: keep objects that sustain MinAreaMS for MinTimeMS
    Objects = ndimage.find_objects(objects_id)
    MinAreaMS_m2 = MinAreaMS * 1000.0**2  # km² → m²
    min_tsteps = int(MinTimeMS / DT)

    ar_tmp = np.zeros_like(objects_id)
    label_out = 1
    for ob in range(n_objects):
        if Objects[ob] is None:
            continue
        obj_slice = Objects[ob]
        nt = objects_id[obj_slice].shape[0]
        if nt < min_tsteps:
            continue
        area_per_t = np.array(
            [
                np.sum(grid_cell_area[obj_slice[1:]][objects_id[obj_slice][tt, :, :] == ob + 1])
                for tt in range(nt)
            ]
        )
        # Require MinAreaMS for MinTimeMS consecutive steps
        area_ok = (area_per_t >= MinAreaMS_m2).astype(int)
        if np.max(np.convolve(area_ok, np.ones(min_tsteps, dtype=int), mode="valid")) == min_tsteps:
            ar_tmp[objects_id == (ob + 1)] = label_out
            label_out += 1

    ar850_objects, _ = clean_up_objects(ar_tmp, min_tsteps=0, dT=DT)

    ar850_objects = BreakupObjects(ar850_objects, min_tsteps, DT)

    if crosses_dateline:
        ar850_objects = ConnectLon_on_timestep(ar850_objects)

    end_time = time.time()
    logger.debug(f"{Fore.GREEN}======> AR 850 hPa tracking: {end_time - start_time:.2f} s")
    logger.info(f"{Style.BRIGHT}AR-850 objects found: {ar850_objects.max()}")

    # -----------------------------------------------------------------------
    # NetCDF output
    # -----------------------------------------------------------------------
    if nc_file is not None:
        logger.debug(f"{Style.BRIGHT} Save AR-850 objects into a NetCDF")

        fino = xr.Dataset(
            {
                "ar850_objects": (
                    ["time", "latitude", "longitude"],
                    ar850_objects.astype(np.int16),
                ),
                "VapTrans": (["time", "latitude", "longitude"], VapTrans.astype(np.float32)),
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
                "time": {
                    "units": "hours since 1900-01-01 00:00:00",
                    "calendar": "standard",
                    "dtype": "int32",
                },
                "ar850_objects": {"zlib": True, "complevel": 5},
                "VapTrans": {"zlib": True, "complevel": 5},
            },
        )
        logger.debug(f"{Style.BRIGHT} AR-850 NetCDF written to {nc_file}")
    else:
        logger.debug(f"{Fore.YELLOW}No output file requested (nc_file=None)")

    return ar850_objects


def AR_IVT_tracking(IVT, times, Lon, Lat, nc_file=None):
    """Track Atmospheric Rivers from Integrated Vapour Transport (IVT).

    Parameters
    ----------
    IVT : ndarray, shape (time, lat, lon), float
        IVT magnitude [kg m⁻¹ s⁻¹] = sqrt(ivte² + ivtn²).
    times : DatetimeIndex
        Timestamps corresponding to the first axis of ``IVT``.
    Lon : ndarray, shape (lat, lon)
        2-D longitude grid [°].
    Lat : ndarray, shape (lat, lon)
        2-D latitude grid [°].
    nc_file : str or None
        If given, write ``ar_ivt_objects`` to this NetCDF path.

    Returns
    -------
    ar_ivt_objects : ndarray, shape (time, lat, lon), int
        Labelled IVT-AR objects; 0 = background.
    """
    start_time = time.time()

    DT = cfg.DT
    IVTthreshold = cfg.IVTthreshold
    MinTimeIVT = cfg.MinTimeIVT

    crosses_dateline = (Lon[0, 0] < -176) and (Lon[0, -1] > 176)

    obj_structure_3D = np.ones((3, 3, 3))

    logger.debug(f"{Style.BRIGHT} Tracking ARs from IVT")

    potIVTs = IVT > IVTthreshold
    objects_id, n_objects = ndimage.label(potIVTs, structure=obj_structure_3D)
    logger.debug(f"{Fore.GREEN} {n_objects} IVT-AR candidates found")

    ar_ivt_objects, _ = clean_up_objects(objects_id, min_tsteps=int(MinTimeIVT / DT), dT=DT)

    ar_ivt_objects = BreakupObjects(ar_ivt_objects, int(MinTimeIVT / DT), DT)

    if crosses_dateline:
        ar_ivt_objects = ConnectLon_on_timestep(ar_ivt_objects)

    end_time = time.time()
    logger.debug(f"{Fore.GREEN}======> AR IVT tracking: {end_time - start_time:.2f} s")
    logger.info(f"{Style.BRIGHT}AR-IVT objects found: {ar_ivt_objects.max()}")

    # -----------------------------------------------------------------------
    # NetCDF output
    # -----------------------------------------------------------------------
    if nc_file is not None:
        logger.debug(f"{Style.BRIGHT} Save AR-IVT objects into a NetCDF")

        fino = xr.Dataset(
            {
                "ar_ivt_objects": (
                    ["time", "latitude", "longitude"],
                    ar_ivt_objects.astype(np.int16),
                ),
                "IVT": (["time", "latitude", "longitude"], IVT.astype(np.float32)),
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
                "time": {
                    "units": "hours since 1900-01-01 00:00:00",
                    "calendar": "standard",
                    "dtype": "int32",
                },
                "ar_ivt_objects": {"zlib": True, "complevel": 5},
                "IVT": {"zlib": True, "complevel": 5},
            },
        )
        logger.debug(f"{Style.BRIGHT} AR-IVT NetCDF written to {nc_file}")
    else:
        logger.debug(f"{Fore.YELLOW}No output file requested (nc_file=None)")

    return ar_ivt_objects
