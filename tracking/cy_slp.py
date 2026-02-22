#!/usr/bin/env python
"""SLP-based cyclone and anticyclone tracking."""

import logging
import time

import numpy as np
import xarray as xr
from scipy import ndimage
from skimage.feature import peak_local_max
from skimage.segmentation import watershed

import atmotrack_config as cfg
from utils import Fore, Style

from .shared import (
    BreakupObjects,
    ConnectLon,
    calc_grid_distance_area,
    clean_up_objects,
    smooth_uniform,
)

logger = logging.getLogger("atmotrack")


def watershed_2d_overlap(
    anom_sel_masked,  # 3D [time,lat,lon] anomaly field for watersheding
    mask_sel,  # 3D [time,lat,lon] objects=True, background=False
    Gridspacing,  # average grid spacing in m
    dT,  # data interval [h]
    connectLon,  # 1 -> connect objects over date line
    min_dist=10,  # min distance between local maxima [index units]
    threshold=1,  # absolute threshold for local maxima [anomaly units]
    mintime=0,  # minimum lifetime of an object [h]
):
    """Break up overlapping 2D objects in 3D via watershed segmentation
    and connect them across timesteps by maximum overlap."""

    label_matrix = mask_sel[:].astype(int)

    if np.max(anom_sel_masked) <= 0:
        anom_sel_masked = anom_sel_masked[:] * -1

    distance = anom_sel_masked[:]
    distance = smooth_uniform(distance, 4, int(750 / (Gridspacing / 1000.0)) * 2)

    local_maxi = np.zeros(distance.shape, dtype=bool)
    markers = np.zeros(distance.shape, dtype=np.int32)
    labels = np.zeros(distance.shape, dtype=np.int32)
    for jj in range(distance.shape[0]):
        coords = peak_local_max(
            distance[jj, :],
            footprint=np.ones((3, 3)),
            labels=label_matrix[jj, :],
            min_distance=min_dist,
            threshold_abs=threshold,
        )
        if coords.size > 0:
            local_maxi[jj, coords[:, 0], coords[:, 1]] = True
        markers[jj, :] = ndimage.label(local_maxi[jj, :, :])[0]
        labels[jj, :] = watershed(
            -distance[jj, :], markers[jj, :], mask=label_matrix[jj, :], connectivity=np.ones((3, 3))
        )

    # --- Connect objects in 3D based on maximum overlap ---
    objects_watershed = np.copy(labels)
    objects_watershed[:] = 0
    ob_max = np.max(labels[0, :]) + 1

    for tt in range(objects_watershed.shape[0]):
        logger.debug(f"watershed_2d_overlap: timestep {tt}/{objects_watershed.shape[0]}")
        if tt == 0:
            objects_watershed[tt, :] = labels[tt, :]
        else:
            obj_t1 = np.unique(labels[tt, :])[1:]
            t0_elements, t0_area = np.unique(objects_watershed[tt - 1, :], return_counts=True)
            t0_elements = t0_elements[1:]
            t0_area = t0_area[1:]

            ob_loc_t0 = ndimage.find_objects(objects_watershed[tt - 1, :])
            valid = np.array([ob_loc_t0[ob] is not None for ob in range(len(ob_loc_t0))])
            try:
                ob_loc_t0 = np.array(ob_loc_t0, dtype=object)[valid]
            except Exception:
                continue
            try:
                ob_loc_t1 = ndimage.find_objects(labels[tt, :])
            except Exception:
                continue

            sort = np.argsort(t0_area)[::-1]
            t0_elements = t0_elements[sort]
            t0_area = t0_area[sort]
            ob_loc_t0 = np.array(ob_loc_t0, dtype=object)[sort]

            for ob in range(len(t0_elements)):
                ob_act = np.copy(objects_watershed[tt - 1, ob_loc_t0[ob][0], ob_loc_t0[ob][1]])
                ob_act[ob_act != t0_elements[ob]] = 0
                ob_act_t1 = np.copy(labels[tt, ob_loc_t0[ob][0], ob_loc_t0[ob][1]])
                ob_t1_overlap = np.unique(ob_act_t1[ob_act == t0_elements[ob]])[1:]
                if len(ob_t1_overlap) == 0:
                    continue
                area_overlap = [
                    np.sum((ob_act > 0) & (ob_act_t1 == ob_t1_overlap[ii]))
                    for ii in range(len(ob_t1_overlap))
                ]
                ob_continue = ob_t1_overlap[np.argmax(area_overlap)]
                ob_area = (
                    labels[tt, ob_loc_t1[ob_continue - 1][0], ob_loc_t1[ob_continue - 1][1]]
                    == ob_continue
                )
                if np.isin(ob_continue, obj_t1) is False:
                    continue
                objects_watershed[tt, ob_loc_t1[ob_continue - 1][0], ob_loc_t1[ob_continue - 1][1]][
                    ob_area
                ] = t0_elements[ob]
                obj_t1 = np.delete(obj_t1, np.where(obj_t1 == ob_continue))

            for ob in range(len(obj_t1)):
                ob_loc_new = ndimage.find_objects((labels[tt, :] == obj_t1[ob]).astype(np.intp))
                ob_new = objects_watershed[tt, :][ob_loc_new[0]]
                ob_new[labels[tt, :][ob_loc_new[0]] == obj_t1[ob]] = ob_max
                objects_watershed[tt, :][ob_loc_new[0]] = ob_new
                ob_max += 1

    objects, _ = clean_up_objects(objects_watershed, min_tsteps=int(mintime / dT), dT=dT)
    return objects


def CY_ACY_slp_tracking(
    slp_data,
    times,
    Lon,
    Lat,
    nc_file=None,
):
    """Track surface cyclones and anticyclones from SLP data (Pa input)."""

    start_time = time.time()

    # Reading tracking parameters
    DT = cfg.DT
    slp_low_anom = cfg.slp_smooth_low_anom
    slp_high_anom = cfg.slp_smooth_high_anom
    MinTimeCY_SLP = cfg.MinTimeCY_SLP
    MinTimeACY_SLP = cfg.MinTimeACY_SLP
    breakup_method = cfg.slp_breakup_method

    # Calculating grid distances and areas
    _, _, grid_cell_area, grid_spacing = calc_grid_distance_area(Lat, Lon)
    grid_cell_area[grid_cell_area < 0] = 0

    obj_structure_3D = np.ones((3, 3, 3))

    # Connect over date line?
    crosses_dateline = False
    if (Lon[0, 0] < -176) & (Lon[0, -1] > 176):
        crosses_dateline = True

    end_time = time.time()
    logger.debug(
        f"{Fore.MAGENTA}======> 'Initialize SLP Cyclone/Anticyclone tracking: {(end_time - start_time):.2f} seconds \n"
    )
    start_time = time.time()

    logger.debug(f"{Style.BRIGHT} Tracking surface cyclones and anticyclones from SLP")

    # Convert Pa -> hPa
    slp = slp_data / 100.0

    # Smooth SLP: 100 km spatial, 1 timestep
    slp_smooth = smooth_uniform(slp, 1, int(100 / (grid_spacing / 1000.0)))

    # Background mean: 78 h temporal, 3000 km spatial
    slp_smooth_mean = smooth_uniform(slp, int(78 / DT), int(3000 / (grid_spacing / 1000.0)))

    slp_anomaly = slp_smooth - slp_smooth_mean

    # --- Cyclones ---
    z_low = slp_anomaly < slp_low_anom
    objects_id_slp_low, low_num_objects = ndimage.label(z_low, structure=obj_structure_3D)
    logger.debug(f"{Fore.GREEN} {low_num_objects} surface cyclone candidates found")

    cy_slp_objects, _ = clean_up_objects(
        objects_id_slp_low, min_tsteps=int(MinTimeCY_SLP / DT), dT=DT
    )

    if breakup_method == "watershed":
        min_dist_idx = int((1000 * 10**3) / grid_spacing)
        low_anom_masked = np.copy(slp_anomaly)
        low_anom_masked[cy_slp_objects == 0] = 0
        cy_slp_objects = watershed_2d_overlap(
            low_anom_masked,
            cy_slp_objects,
            grid_spacing,
            DT,
            int(crosses_dateline),
            min_dist=min_dist_idx,
            threshold=1,
            mintime=MinTimeCY_SLP,
        )
    else:
        cy_slp_objects = BreakupObjects(cy_slp_objects, min_tsteps=int(MinTimeCY_SLP / DT), dT=DT)

    if crosses_dateline:
        cy_slp_objects = ConnectLon(cy_slp_objects)

    # --- Anticyclones ---
    z_high = slp_anomaly > slp_high_anom
    objects_id_slp_high, hi_num_objects = ndimage.label(z_high, structure=obj_structure_3D)
    logger.debug(f"{Fore.GREEN} {hi_num_objects} surface anticyclone candidates found")

    acy_slp_objects, _ = clean_up_objects(
        objects_id_slp_high, min_tsteps=int(MinTimeACY_SLP / DT), dT=DT
    )

    if breakup_method == "watershed":
        min_dist_idx = int((1000 * 10**3) / grid_spacing)
        high_anom_masked = np.copy(slp_anomaly)
        high_anom_masked[acy_slp_objects == 0] = 0
        acy_slp_objects = watershed_2d_overlap(
            high_anom_masked,
            acy_slp_objects,
            grid_spacing,
            DT,
            int(crosses_dateline),
            min_dist=min_dist_idx,
            threshold=1,
            mintime=MinTimeACY_SLP,
        )
    else:
        acy_slp_objects = BreakupObjects(
            acy_slp_objects, min_tsteps=int(MinTimeACY_SLP / DT), dT=DT
        )

    if crosses_dateline:
        acy_slp_objects = ConnectLon(acy_slp_objects)

    end_time = time.time()
    logger.debug(
        f"{Fore.GREEN}======> 'SLP cyclone/anticyclone tracking: {(end_time - start_time):.2f} seconds \n"
    )
    start_time = time.time()

    ######################################################################
    #####################################################################

    if nc_file is not None:
        logger.debug(f"{Style.BRIGHT} Save SLP objects into a netCDF")

        fino = xr.Dataset(
            {
                "cy_slp_objects": (["time", "latitude", "longitude"], cy_slp_objects),
                "acy_slp_objects": (["time", "latitude", "longitude"], acy_slp_objects),
                "slp": (["time", "latitude", "longitude"], slp_data),
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
                "slp": {"zlib": True, "complevel": 5},
                "cy_slp_objects": {"zlib": True, "complevel": 5},
                "acy_slp_objects": {"zlib": True, "complevel": 5},
            },
        )

        end_time = time.time()
        logger.debug(
            f"{Style.BRIGHT} ======> 'Writing SLP files: {(end_time - start_time):.2f} seconds \n"
        )

    else:
        logger.debug(f"{Fore.YELLOW}No writing files required, output file name is empty")

    return cy_slp_objects, acy_slp_objects
