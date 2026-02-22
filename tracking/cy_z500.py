#!/usr/bin/env python
"""Z500-based cyclone and anticyclone tracking."""

import logging
import time

import numpy as np
import scipy.ndimage as filters
import xarray as xr
from scipy import ndimage

import atmotrack_config as cfg
from constants import const
from utils import Fore, Style

from .shared import BreakupObjects, ConnectLon, calc_grid_distance_area, clean_up_objects

logger = logging.getLogger("atmotrack")


def CY_ACY_z500_tracking(z500_data, times, Lon, Lat, nc_file=None):

    start_time = time.time()

    # Reading tracking parameters
    # Time step in hours
    DT = cfg.DT

    z500_low_anom = cfg.z500_smooth_low_anom
    z500_high_anom = cfg.z500_smooth_high_anom
    MinTimeCY = cfg.MinTimeCY
    MinTimeACY = cfg.MinTimeACY
    z500_smooth_method = cfg.z500_smooth_method
    z500_smooth_scale_km = cfg.z500_smooth_scale_km

    # Calculating grid distances and areas
    _, _, grid_cell_area, grid_spacing = calc_grid_distance_area(Lat, Lon)
    grid_cell_area[grid_cell_area < 0] = 0

    obj_structure_3D = np.ones((3, 3, 3))

    times[0]

    # connect over date line?
    crosses_dateline = False
    if (Lon[0, 0] < -176) & (Lon[0, -1] > 176):
        crosses_dateline = True

    end_time = time.time()
    logger.debug(
        f"{Fore.MAGENTA}======> 'Initialize z500 Cyclone/Anticyclone tracking function: {(end_time - start_time):.2f} seconds \n"
    )
    start_time = time.time()

    # --------------------------------------------------------
    # TRACKING z500 anomaly OBJECTS
    # --------------------------------------------------------
    logger.debug(f"{Style.BRIGHT} Tracking 500 hPa cyclones and anticyclones")

    # Divide by gravity to get geopotential height
    z500 = z500_data / const.g

    spatial_steps = int(z500_smooth_scale_km / (grid_spacing / 1000.0))
    if z500_smooth_method == "gaussian":
        z500_smooth = filters.gaussian_filter(z500, sigma=(0, spatial_steps, spatial_steps))
    else:
        z500_smooth = filters.uniform_filter(z500, size=(1, spatial_steps, spatial_steps))

    z500_smooth_mean = filters.uniform_filter(
        z500,
        size=(
            int(78 / DT),
            int(3000 / (grid_spacing / 1000.0)),
            int(3000 / (grid_spacing / 1000.0)),
        ),
    )

    z500_smooth_anom = z500_smooth - z500_smooth_mean

    z_low = z500_smooth_anom < z500_low_anom
    z_high = z500_smooth_anom > z500_high_anom

    objects_id_z500_low, low_num_objects = ndimage.label(z_low, structure=obj_structure_3D)
    logger.debug(f"{Fore.GREEN} {low_num_objects} cyclones found")

    objects_id_z500_high, hi_num_objects = ndimage.label(z_high, structure=obj_structure_3D)
    logger.debug(f"{Fore.GREEN} {hi_num_objects} anticyclones found")

    # connect objects over date line
    if crosses_dateline:
        objects_id_z500_low = ConnectLon(objects_id_z500_low)
        objects_id_z500_high = ConnectLon(objects_id_z500_high)

    # # get indices of object to reduce memory requirements during manipulation
    # object_indices_low = ndimage.find_objects(objects_id_z500_low)
    # object_indices_high = ndimage.find_objects(objects_id_z500_high)

    # Clean up objects
    cy_z500_objects, _ = clean_up_objects(
        objects_id_z500_low, min_tsteps=int(MinTimeCY / DT), dT=DT
    )
    acy_z500_objects, _ = clean_up_objects(
        objects_id_z500_high, min_tsteps=int(MinTimeACY / DT), dT=DT
    )

    cy_z500_objects = BreakupObjects(cy_z500_objects, min_tsteps=int(MinTimeCY / DT), dT=DT)
    acy_z500_objects = BreakupObjects(acy_z500_objects, min_tsteps=int(MinTimeACY / DT), dT=DT)

    end_time = time.time()
    logger.debug(
        f"{Fore.GREEN}======> 'z500 cyclone/anticyclone tracking: {(end_time - start_time):.2f} seconds \n"
    )
    start_time = time.time()

    ######################################################################
    #####################################################################

    if nc_file is not None:
        logger.debug(f"{Style.BRIGHT} Save objects into a netCDF")

        fino = xr.Dataset(
            {
                "cy_z500_objects": (["time", "latitude", "longitude"], cy_z500_objects),
                "acy_z500_objects": (["time", "latitude", "longitude"], acy_z500_objects),
                "z500": (["time", "latitude", "longitude"], z500_data),
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
                "time": {"units": "hours since 1900-01-01 00:00:00", "calendar": "standard", "dtype": "int32"},
                "z500": {"zlib": True, "complevel": 5},
                "cy_z500_objects": {"zlib": True, "complevel": 5},
                "acy_z500_objects": {"zlib": True, "complevel": 5},
            },
        )

        end_time = time.time()
        logger.debug(
            f"{Style.BRIGHT} ======> 'Writing files: {(end_time - start_time):.2f} seconds \n"
        )
        start_time = time.time()

    else:
        logger.debug(f"{Fore.YELLOW}No writing files required, output file name is empty")

    return cy_z500_objects, acy_z500_objects
