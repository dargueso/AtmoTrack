#!/usr/bin/env python
"""MCS tracking."""

import logging
import time

import numpy as np
import scipy.ndimage as filters
import xarray as xr
from scipy import ndimage

import atmotrack_config as cfg

from .shared import (
    ConnectLon,
    calc_grid_distance_area,
    calc_object_characteristics,
    calculate_area_objects,
    remove_small_short_objects,
)


def MCS_tracking(pr_data, bt_data, times, Lon, Lat, nc_file):
    """Function to track MCS from precipitation and brightness temperature"""

    start_time = time.time()
    # Reading tracking parameters

    DT = cfg.DT

    # Precipitation tracking setup
    smooth_sigma_pr = cfg.smooth_sigma_pr  # [0] Gaussion std for precipitation smoothing
    thres_pr = cfg.thres_pr  # [2] precipitation threshold [mm/h]
    min_time_pr = cfg.min_time_pr  # [3] minum lifetime of PR feature in hours
    min_area_pr = cfg.min_area_pr  # [5000] minimum area of precipitation feature in km2
    # Brightness temperature (Tb) tracking setup
    smooth_sigma_bt = cfg.smooth_sigma_bt  #  [0] Gaussion std for Tb smoothing
    thres_bt = cfg.thres_bt  # [241] minimum Tb of cloud shield
    min_time_bt = cfg.min_time_bt  # [9] minium lifetime of cloud shield in hours
    min_area_bt = cfg.min_area_bt  # [40000] minimum area of cloud shield in km2
    # MCs detection
    MCS_min_area = cfg.MCS_min_area  # [5000] km2
    MCS_thres_pr = cfg.MCS_thres_pr  # [10] minimum max precipitation in mm/h
    MCS_thres_peak_pr = cfg.MCS_thres_peak_pr  # [10] Minimum lifetime peak of MCS precipitation
    MCS_thres_bt = cfg.MCS_thres_bt  # [225] minimum brightness temperature
    MCS_min_area_bt = cfg.MCS_min_area_bt  # [40000] min cloud area size in km2
    MCS_min_time = cfg.MCS_min_time  # [4] minimum time step

    # Calculating grid distances and areas

    _, _, grid_cell_area, grid_spacing = calc_grid_distance_area(Lat, Lon)
    grid_cell_area[grid_cell_area < 0] = 0

    obj_structure_3D = np.ones((3, 3, 3))

    start_day = times[0]

    # connect over date line?
    crosses_dateline = False
    if (Lon[0, 0] < -176) & (Lon[0, -1] > 176):
        crosses_dateline = True

    end_time = time.time()
    logging.debug(
        f"======> 'Initialize MCS tracking function: {(end_time - start_time):.2f} seconds \n"
    )
    start_time = time.time()
    # --------------------------------------------------------
    # TRACKING PRECIP OBJECTS
    # --------------------------------------------------------
    logging.debug("        track  precipitation")

    pr_smooth = filters.gaussian_filter(pr_data, sigma=(0, smooth_sigma_pr, smooth_sigma_pr))
    pr_mask = pr_smooth >= thres_pr * DT
    objects_id_pr, num_objects = ndimage.label(pr_mask, structure=obj_structure_3D)
    logging.debug("            " + str(num_objects) + " precipitation object found")

    # connect objects over date line
    if crosses_dateline:
        objects_id_pr = ConnectLon(objects_id_pr)

    # get indices of object to reduce memory requirements during manipulation
    object_indices = ndimage.find_objects(objects_id_pr)

    # Calcualte area of objects
    area_objects = calculate_area_objects(objects_id_pr, object_indices, grid_cell_area)

    # Keep only large and long enough objects
    # Remove objects that are too small or short lived
    pr_objects = remove_small_short_objects(
        objects_id_pr, area_objects, min_area_pr, min_time_pr, DT
    )

    calc_object_characteristics(
        pr_objects,  # feature object file
        pr_data,  # original file used for feature detection
        f"{cfg.path_in}/PR_{start_day.year}{start_day.month:02d}",
        times,  # timesteps of the data
        Lat,  # 2D latidudes
        Lon,  # 2D Longitudes
        grid_spacing,
        grid_cell_area,
        min_tsteps=int(min_time_pr / DT),  # minimum lifetime in data timesteps
    )

    end_time = time.time()
    logging.debug(f"======> 'Tracking precip: {(end_time - start_time):.2f} seconds \n")
    start_time = time.time()
    # --------------------------------------------------------
    # TRACKING CLOUD (BT) OBJECTS
    # --------------------------------------------------------
    logging.debug("            track  clouds")
    bt_smooth = filters.gaussian_filter(bt_data, sigma=(0, smooth_sigma_bt, smooth_sigma_bt))
    bt_mask = bt_smooth <= thres_bt
    objects_id_bt, num_objects = ndimage.label(bt_mask, structure=obj_structure_3D)
    logging.debug("            " + str(num_objects) + " cloud object found")

    # connect objects over date line
    if crosses_dateline:
        logging.debug("            connect cloud objects over date line")
        objects_id_bt = ConnectLon(objects_id_bt)

    # get indices of object to reduce memory requirements during manipulation
    object_indices = ndimage.find_objects(objects_id_bt)

    # Calcualte area of objects
    area_objects = calculate_area_objects(objects_id_bt, object_indices, grid_cell_area)

    # Keep only large and long enough objects
    # Remove objects that are too small or short lived
    bt_objects = remove_small_short_objects(
        objects_id_bt, area_objects, min_area_bt, min_time_bt, DT
    )

    end_time = time.time()
    logging.debug(f"======> 'Tracking clouds: {(end_time - start_time):.2f} seconds \n")
    start_time = time.time()

    # logging.debug("            break up long living cloud shield objects that heve many elements")
    # bt_objects = BreakupObjects(bt_objects, int(min_time_bt / DT), DT)

    end_time = time.time()
    logging.debug(f"======> 'Breaking up cloud objects: {(end_time - start_time):.2f} seconds \n")
    start_time = time.time()

    calc_object_characteristics(
        bt_objects,  # feature object file
        bt_data,  # original file used for feature detection
        f"{cfg.path_in}/BT_{start_day.year}{start_day.month:02d}",
        times,  # timesteps of the data
        Lat,  # 2D latidudes
        Lon,  # 2D Longitudes
        grid_spacing,
        grid_cell_area,
        min_tsteps=int(min_time_bt / DT),  # minimum lifetime in data timesteps
    )
    end_time = time.time()
    logging.debug(
        f"======> 'Calculate cloud characteristics: {(end_time - start_time):.2f} seconds \n"
    )
    start_time = time.time()
    # --------------------------------------------------------
    # CHECK IF PR OBJECTS QUALIFY AS MCS
    # (or selected strom type according to msc_config.py)
    # --------------------------------------------------------
    logging.debug("            check if pr objects quallify as MCS (or selected storm type)")
    # check if precipitation object is from an MCS
    object_indices = ndimage.find_objects(pr_objects)
    MCS_objects = np.zeros(pr_objects.shape, dtype=int)

    for iobj, _ in enumerate(object_indices):
        if object_indices[iobj] is None:
            continue

        time_slice = object_indices[iobj][0]
        lat_slice = object_indices[iobj][1]
        lon_slice = object_indices[iobj][2]

        pr_object_slice = pr_objects[object_indices[iobj]]
        pr_object_act = np.where(pr_object_slice == iobj + 1, True, False)

        if len(pr_object_act) < 2:
            continue

        pr_slice = pr_data[object_indices[iobj]]
        pr_act = np.copy(pr_slice)
        pr_act[~pr_object_act] = 0

        bt_slice = bt_data[object_indices[iobj]]
        bt_act = np.copy(bt_slice)
        bt_act[~pr_object_act] = 0

        bt_object_slice = bt_objects[object_indices[iobj]]
        bt_object_act = np.copy(bt_object_slice)
        bt_object_act[~pr_object_act] = 0

        area_act = np.tile(grid_cell_area[lat_slice, lon_slice], (pr_act.shape[0], 1, 1))
        area_act[~pr_object_act] = 0

        pr_size = np.array(np.sum(area_act, axis=(1, 2)))
        pr_max = np.array(np.max(pr_act, axis=(1, 2)))

        # Check overlaps between clouds (bt) and precip objects
        objects_overlap = np.delete(np.unique(bt_object_act[pr_object_act]), 0)

        if len(objects_overlap) == 0:
            # no deep cloud shield is over the precipitation
            continue

        ## Keep bt objects (entire) that partially overlap with pr object

        bt_object_overlap = np.isin(bt_objects[time_slice].flatten(), objects_overlap).reshape(
            bt_objects[time_slice].shape
        )

        # Get size of all cloud (bt) objects together
        # We get size of all cloud objects that overlap partially with pr object
        # DO WE REALLY NEED THIS?

        bt_size = np.array(
            [
                np.sum(grid_cell_area[bt_object_overlap[tt, :, :] > 0])
                for tt in range(bt_object_overlap.shape[0])
            ]
        )

        # Check if BT is below threshold over precip areas
        bt_min_temp = np.nanmin(np.where(bt_object_slice > 0, bt_slice, 999), axis=(1, 2))

        # minimum lifetime peak precipitation
        is_pr_peak_intense = np.max(pr_max) >= MCS_thres_peak_pr * DT
        MCS_test = (
            (bt_size / 1000**2 >= MCS_min_area_bt)
            & (bt_min_temp <= MCS_thres_bt)
            & (pr_size / 1000**2 >= MCS_min_area)
            & (pr_max >= MCS_thres_pr * DT)
            & (is_pr_peak_intense)
        )

        # assign unique object numbers

        pr_object_act = np.array(pr_object_act).astype(int)
        pr_object_act[pr_object_act == 1] = iobj + 1

        window_length = int(MCS_min_time / DT)
        moving_averages = np.convolve(MCS_test, np.ones(window_length), "valid") / window_length
        if (len(moving_averages) > 0) & (np.max(moving_averages) == 1):
            TMP = np.copy(MCS_objects[object_indices[iobj]])
            TMP = TMP + pr_object_act
            MCS_objects[object_indices[iobj]] = TMP

        else:
            continue

    objects_id_MCS, num_objects = ndimage.label(MCS_objects, structure=obj_structure_3D)
    grMCSs = calc_object_characteristics(
        objects_id_MCS,  # feature object file
        pr_data,  # original file used for feature detection
        f"{cfg.path_in}/MCS_{start_day.year}{start_day.month:02d}",
        times,  # timesteps of the data
        Lat,  # 2D latidudes
        Lon,  # 2D Longitudes
        grid_spacing,
        grid_cell_area,
        min_tsteps=int(MCS_min_time / DT),  # minimum lifetime in data timesteps
    )

    end_time = time.time()
    logging.debug(f"======> 'MCS tracking: {(end_time - start_time):.2f} seconds \n")
    start_time = time.time()

    ###########################################################
    ###########################################################
    ## WRite netCDF with xarray
    if nc_file is not None:
        logging.debug("Save objects into a netCDF")

        fino = xr.Dataset(
            {
                "MCS_objects": (["time", "y", "x"], objects_id_MCS),
                "PR": (["time", "y", "x"], pr_data),
                "PR_objects": (["time", "y", "x"], objects_id_pr),
                "BT": (["time", "y", "x"], bt_data),
                "BT_objects": (["time", "y", "x"], objects_id_bt),
                "lat": (["y", "x"], Lat),
                "lon": (["y", "x"], Lon),
            },
            coords={"time": times.values},
        )

        fino.to_netcdf(
            nc_file,
            mode="w",
            encoding={
                "PR": {"zlib": True, "complevel": 5},
                "PR_objects": {"zlib": True, "complevel": 5},
                "BT": {"zlib": True, "complevel": 5},
                "BT_objects": {"zlib": True, "complevel": 5},
                "MCS_objects": {"zlib": True, "complevel": 5},
            },
        )

        end_time = time.time()
        logging.debug(f"======> 'Writing files: {(end_time - start_time):.2f} seconds \n")
        start_time = time.time()
    else:
        logging.debug("No writing files required, output file name is empty")
    ###########################################################
    ###########################################################
    # ============================
    # Write NetCDF
    return grMCSs, MCS_objects
