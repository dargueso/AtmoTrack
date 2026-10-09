#!/usr/bin/env python
"""Z500-based cyclone and anticyclone tracking."""

import logging
import time

import numpy as np
import scipy.ndimage as filters
from scipy import ndimage

from atmotrack import config as cfg
from atmotrack.constants import const
from atmotrack.grid import Grid
from atmotrack.output import write_tracking_file
from atmotrack.utils import Fore, Style

from .shared import BreakupObjects, ConnectLon, clean_up_objects, smooth_uniform

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

    # Grid description (regular or curvilinear, any lat order / lon convention)
    grid = Grid(Lon, Lat)
    grid_spacing = grid.spacing

    obj_structure_3D = np.ones((3, 3, 3))

    # connect over the longitude seam of a global grid?
    crosses_dateline = grid.is_global_periodic

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
        z500_smooth = smooth_uniform(z500, 1, spatial_steps)

    # Background: 78 h temporal, 3000 km spatial running mean
    z500_smooth_mean = smooth_uniform(z500, int(78 / DT), int(3000 / (grid_spacing / 1000.0)))

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

        write_tracking_file(
            nc_file,
            times,
            grid,
            {
                "cy_z500_objects": (
                    cy_z500_objects,
                    {"long_name": "500 hPa cyclone object labels"},
                ),
                "acy_z500_objects": (
                    acy_z500_objects,
                    {"long_name": "500 hPa anticyclone object labels"},
                ),
                "z500": (z500_data, {"units": "m**2 s**-2", "long_name": "500 hPa geopotential"}),
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
