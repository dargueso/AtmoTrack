#!/usr/bin/env python
"""Cut-off low and front tracking."""

import logging
import time

import metpy.calc as calc
import numpy as np
import xarray as xr
from scipy import ndimage

import atmotrack_config as cfg
from constants import const
from utils import Fore, Style

from .shared import calc_grid_distance_area, haversine, split_objects

logger = logging.getLogger("atmotrack")


def Front_tracking(u850, v850, t850, times, Lon, Lat, Mask=None):
    """
    Calculate front objects based on u850, v850, and t850 fields.

    Parameters:
        u850 (ndarray): Zonal wind component at 850 hPa.
        v850 (ndarray): Meridional wind component at 850 hPa.
        t850 (ndarray): Temperature field at 850 hPa.
        Lat (ndarray): Latitude array.
        Lon (ndarray): Longitude array.
        Mask (ndarray, optional): Mask to exclude regions (e.g., tropics) if needed.

    Returns:
        ndarray: Array of front objects, labeled with unique integer values.
    """

    # Reading tracking parameters

    front_treshold = cfg.front_treshold
    MinAreaFR = cfg.MinAreaFR

    # Calculate the horizontal derivatives of u and v

    dx, dy, _, _ = calc_grid_distance_area(Lat, Lon)

    du = np.gradient(np.array(u850))
    dv = np.gradient(np.array(v850))

    # Calculate potential vorticity term for frontal detection
    PV = np.abs(dv[-1] / dx[None, :] - du[-2] / dy[None, :])

    # Calculate the temperature gradient magnitude
    vgrad = np.gradient(np.array(t850), axis=(1, 2))
    Tgrad = np.sqrt(vgrad[0] ** 2 + vgrad[1] ** 2)

    # Calculate Fstar, the frontal diagnostic variable
    Fstar = PV * Tgrad

    # Set a threshold for temperature gradient (optional, based on literature)
    Tgrad_zero = 0.45  # Assumed threshold in K/(100 km)

    # Calculate the Coriolis parameter
    CoriolisPar = calc.coriolis_parameter(np.deg2rad(Lat)).magnitude

    # Calculate the Frontal Diagnostic
    Frontal_Diagnostic = np.array(Fstar / (CoriolisPar * Tgrad_zero))

    # Apply mask to exclude regions if necessary
    if Mask is not None:
        FrontMask = np.copy(Mask)
        FrontMask[np.abs(Lat) < 10] = 0  # Exclude tropics by default
        Frontal_Diagnostic = np.abs(Frontal_Diagnostic)
        Frontal_Diagnostic[:, FrontMask == 0] = 0

    # Define a structural element for connected components
    rgiObj_Struct_Fronts = np.zeros((3, 3, 3))
    rgiObj_Struct_Fronts[1, :, :] = 1

    # Apply the front threshold to create a binary mask
    Fmask = Frontal_Diagnostic > front_treshold

    # Label connected regions that exceed the threshold
    rgiObjectsUD, nr_objectsUD = ndimage.label(Fmask, structure=rgiObj_Struct_Fronts)
    logger.debug(f"{Fore.GREEN}  {str(nr_objectsUD)} object(s) found")

    # Define the grid cell area if not provided
    # Assume approximately equal area if you don't have exact Area data
    if Mask is not None:
        Area = np.copy(Mask) * (dx * dy)
    else:
        Area = np.ones_like(Frontal_Diagnostic) * (dx * dy)

    # Calculate the area of each object
    Objects = ndimage.find_objects(rgiObjectsUD)

    rgiAreaObj = []

    # Loop through each identified object
    for ob in range(nr_objectsUD):
        # Get the slice for the current object
        area_slice = Area[Objects[ob]]
        object_slice = rgiObjectsUD[Objects[ob]]

        # Calculate the area of the current object where the label matches (ob + 1)
        object_area = np.sum(area_slice[object_slice == ob + 1])

        # Append the computed area to the list
        rgiAreaObj.append(object_area)

    # Convert the list to a NumPy array for further processing
    rgiAreaObj = np.array(rgiAreaObj)

    # Create the final object array, excluding small objects
    FR_objects = np.copy(rgiObjectsUD)
    TooSmall = np.where(rgiAreaObj < MinAreaFR * 1000**2)  # Convert MinAreaFR to square meters
    FR_objects[np.isin(FR_objects, TooSmall[0] + 1)] = 0

    return FR_objects


def COL_tracking(
    cy_z500_objects,
    z500_data,
    u200_data,
    u850_data,
    v850_data,
    t850_data,
    pr_data,
    pr_data_max,
    times=None,
    Lon=None,
    Lat=None,
    nc_file=None,
):
    """Function to determine if a cyclone is a cut-off low"""

    start_time = time.time()

    # Reading tracking parameters
    col_buffer = cfg.col_buffer
    col_region = cfg.col_region
    DT = cfg.DT
    col_min_dur = cfg.col_min_dur
    MaxDistCYFeatures = cfg.MaxDistCYFeatures
    col_z500_threshold_min = cfg.col_z500_threshold_min
    col_percent_isolation = cfg.col_percent_isolation
    col_ring_isolation = cfg.col_ring_isolation
    col_thres_isolation = cfg.col_thres_isolation

    col_min_lat = cfg.col_min_lat
    col_max_lat = cfg.col_max_lat
    col_min_lon = cfg.col_min_lon
    col_max_lon = cfg.col_max_lon

    # Calculating grid distances and areas
    _, _, grid_cell_area, grid_spacing = calc_grid_distance_area(Lat, Lon)
    grid_cell_area[grid_cell_area < 0] = 0

    # Check if cyclone is a cut-off low
    cy_z500_objects = split_objects(
        cy_z500_objects, distance_threshold=int(MaxDistCYFeatures / grid_spacing)
    )

    object_indices_low = ndimage.find_objects(cy_z500_objects.astype(int))

    col_objects = np.zeros(cy_z500_objects.shape, dtype=int)

    y_size = Lat.shape[0]
    x_size = Lon.shape[1]

    front_objects = Front_tracking(u850_data, v850_data, t850_data, times, Lon, Lat)

    for iobj in range(len(object_indices_low)):
        if object_indices_low[iobj] is None:
            continue

        obj_act = cy_z500_objects[object_indices_low[iobj]] == iobj + 1
        if obj_act.shape[0] < col_min_dur / DT:
            # print('object too short')
            continue

        time_start = object_indices_low[iobj][0].start
        time_stop = object_indices_low[iobj][0].stop
        lat_start = object_indices_low[iobj][1].start - int(col_buffer / grid_spacing)
        lat_stop = object_indices_low[iobj][1].stop + int(col_buffer / grid_spacing)
        lon_start = object_indices_low[iobj][2].start - int(col_buffer / grid_spacing)
        lon_stop = object_indices_low[iobj][2].stop + int(col_buffer / grid_spacing)

        if lat_start < 0:
            lat_start = 0
        if lon_start < 0:
            lon_start = 0
        if lat_stop > z500_data.shape[1]:
            lat_stop = z500_data.shape[1]
        if lon_stop > z500_data.shape[2]:
            lon_stop = z500_data.shape[2]

        z500_slice = z500_data[time_start:time_stop, lat_start:lat_stop, lon_start:lon_stop]
        u200_slice = u200_data[time_start:time_stop, lat_start:lat_stop, lon_start:lon_stop]
        object_slice = (
            np.copy(cy_z500_objects[time_start:time_stop, lat_start:lat_stop, lon_start:lon_stop])
            == iobj + 1
        )
        front_slice = front_objects[time_start:time_stop, lat_start:lat_stop, lon_start:lon_stop]
        lat_slice = Lat[lat_start:lat_stop, lon_start:lon_stop]
        lon_slice = Lon[lat_start:lat_stop, lon_start:lon_stop]

        # find location of z500 minimum
        z500_slice_obj = np.copy(z500_slice)
        z500_slice_obj[object_slice == 0] = np.nan

        logger.debug(
            f"{Fore.GREEN} Cyclone {iobj + 1} starts at {times[time_start].strftime('%Y-%m-%d %HUTC')}"
        )
        for tt in range(z500_slice_obj.shape[0]):
            if np.isnan(z500_slice_obj[tt]).all():
                # no object to process
                logger.debug(
                    f"{Fore.YELLOW} Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is not COL because there is no object"
                )
                object_slice[tt, :, :] = 0
                continue
            min_loc = np.nanargmin(z500_slice_obj[tt, :, :])
            min_la = np.unravel_index(min_loc, z500_slice_obj[tt, :, :].shape)[0]
            min_lo = np.unravel_index(min_loc, z500_slice_obj[tt, :, :].shape)[1]

            la0 = min_la - int(col_buffer / grid_spacing)
            lo0 = min_lo - int(col_buffer / grid_spacing)
            la1 = min_la + int(col_buffer / grid_spacing) + 1
            lo1 = min_lo + int(col_buffer / grid_spacing) + 1

            if la0 < 0:
                la0 = 0
            if lo0 < 0:
                lo0 = 0

            if la1 > z500_slice_obj.shape[1]:
                la1 = z500_slice_obj.shape[1]

            if lo1 > z500_slice_obj.shape[2]:
                lo1 = z500_slice_obj.shape[2]

            lat_reg = lat_slice[la0:la1, lo0:lo1]
            lon_reg = lon_slice[la0:la1, lo0:lo1]

            z500_reg = z500_slice[tt, la0:la1, lo0:lo1]
            u200_reg = u200_slice[tt, la0:la1, lo0:lo1]
            z500_reg_obj = z500_slice_obj[tt, la0:la1, lo0:lo1]
            object_reg = object_slice[tt, la0:la1, lo0:lo1]
            front_slice[tt, la0:la1, lo0:lo1]
            min_z500_obj = z500_slice[tt, min_la, min_lo]

            # Check if radius around center has higher Z
            min_loc_tt = np.nanargmin(z500_reg_obj)
            min_la_tt = np.unravel_index(min_loc_tt, z500_reg_obj.shape)[0]
            min_lo_tt = np.unravel_index(min_loc_tt, z500_reg_obj.shape)[1]

            # COL should only occure between 20 and 70 degrees
            # https://journals.ametsoc.org/view/journals/clim/33/6/jcli-d-19-0497.1.xml
            if (abs(lat_reg[min_la_tt, min_lo_tt]) < 20) | (
                abs(lat_reg[min_la_tt, min_lo_tt]) > 70
            ):
                logger.debug(
                    f"{Fore.YELLOW}Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is not COL because of latitude (not in 20-70)"
                )
                object_slice[tt, :, :] = 0
                continue

            # remove cyclones that are close to the poles
            if np.max(np.abs(lat_reg)) > 88:
                logger.debug(
                    f"{Fore.YELLOW}Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is too close to the poles"
                )
                object_slice[tt, :, :] = 0
                continue

            if (
                (np.max(lat_reg[object_reg[:, :] == 1]) > col_max_lat)
                | (np.min(lat_reg[object_reg[:, :] == 1]) < col_min_lat)
                | (np.max(lon_reg[object_reg[:, :] == 1]) > col_max_lon)
                | (np.min(lon_reg[object_reg[:, :] == 1]) < col_min_lon)
            ):
                logger.debug(
                    f"{Fore.YELLOW}Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is too far north or south (or next to the border)"
                )
                object_slice[tt, :, :] = 0
                continue

            if (~np.isnan(z500_slice_obj[tt])).sum == 0:
                # no object to process
                object_slice[tt, :, :] = 0
                continue

            # CRITERIA 1) at least col_percent_isolation*100 % of grid cells in ring have have 100 m higher Z than center

            rdist = haversine(
                lat_reg[min_la_tt, min_lo_tt], lon_reg[min_la_tt, min_lo_tt], lat_reg, lon_reg
            )

            ring = (rdist >= (col_ring_isolation - (grid_spacing) * 2)) & (
                rdist <= (col_ring_isolation + (grid_spacing) * 2)
            )
            if (
                np.sum((z500_reg[ring] - min_z500_obj) / const.g > col_thres_isolation)
                < np.sum(ring) * col_percent_isolation
            ):
                logger.debug(
                    f"{Fore.YELLOW}Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is not COL because is not detached enough"
                )
                object_slice[tt, :, :] = 0
                continue

            # CRITERIA 2) check if 200 hPa wind speed is eastward in the poleward direction of the cyclone
            if lat_reg[min_la_tt, min_lo_tt] > 0:
                east_flow = u200_reg[0:min_la_tt, min_lo_tt]
            else:
                east_flow = u200_reg[min_la_tt:-1, min_lo_tt]

            if east_flow.shape[0] != 0:
                if np.min(east_flow) > 0:
                    logger.debug(
                        f"{Fore.YELLOW}Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is not COL because of eastward flow"
                    )
                    object_slice[tt, :, :] = 0
                    continue
            elif east_flow.shape[0] == 0:
                logger.debug(
                    f"{Fore.YELLOW}Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is not COL because of eastward flow, too close to upper boundary"
                )
                object_slice[tt, :, :] = 0
                continue

            # CRITERIA 3) check if there is a front in the region

            # front_test = np.sum(np.abs(front_reg[:, min_lo_tt:]) > 1)
            # if front_test < 1:
            #     logger.debug(f'{Fore.YELLOW}yclone {iobj+1} at {tt} is not COL because of no front to the east')
            #     object_slice[tt,:,:] = 0
            #     continue
            if (min_z500_obj / const.g) > col_z500_threshold_min:
                logger.debug(
                    f"{Fore.YELLOW}Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is not COL because of z500 threshold (not deep enough)"
                )
                object_slice[tt, :, :] = 0
                continue

            # CRITERIA 4) Check if system is within region
            obj_mass_center = ndimage.center_of_mass(object_slice[tt, :, :])
            obj_track_lat = lat_slice[
                int(round(obj_mass_center[0])), int(round(obj_mass_center[1]))
            ]
            obj_track_lon = lon_slice[
                int(round(obj_mass_center[0])), int(round(obj_mass_center[1]))
            ]
            if (
                obj_track_lat < col_region[2]
                or obj_track_lat > col_region[3]
                or obj_track_lon < col_region[0]
                or obj_track_lon > col_region[1]
            ):
                logger.debug(
                    f"{Fore.YELLOW}Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is outside {col_region}"
                )

                object_slice[tt, :, :] = 0
                continue

            # # CRITERIA 5) Check if system is not too close to borders (not closed system)

            if lat_start == 0 or lat_stop == y_size or lon_start == 0 or lon_stop == x_size:
                if lat_start == 0 and np.any(object_slice[tt, 0, :] == 1):
                    logger.debug(
                        f"{Fore.YELLOW}Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is too close to the border"
                    )
                    object_slice[tt, :, :] = 0
                    continue
                if lat_stop == y_size and np.any(object_slice[tt, -1, :] == 1):
                    logger.debug(
                        f"{Fore.YELLOW}Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is too close to the border"
                    )
                    object_slice[tt, :, :] = 0
                    continue
                if lon_start == 0 and np.any(object_slice[tt, :, 0] == 1):
                    logger.debug(
                        f"{Fore.YELLOW}Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is too close to the border"
                    )
                    object_slice[tt, :, :] = 0
                    continue
                if lon_stop == x_size and np.any(object_slice[tt, :, -1] == 1):
                    logger.debug(
                        f"{Fore.YELLOW}Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is too close to the border"
                    )
                    object_slice[tt, :, :] = 0
                    continue

        # CRITERIA 6) Remove objects that are too short after all checks
        obj_life = object_slice.sum(axis=(1, 2)) > 0
        obj_life_true = np.where(obj_life)[0]

        if obj_life_true.size == 0:
            continue
        else:
            extended_obj_life = np.arange(obj_life_true[0], obj_life_true[-1] + 1)

        if extended_obj_life.size < col_min_dur / DT:
            logger.debug(
                f"{Fore.YELLOW}Cyclone {iobj + 1} is not COL because it is too short after all other criteria applied"
            )
            continue
        else:
            logger.debug(
                f"{Fore.GREEN}Cyclone {iobj + 1} at {times[time_start + tt].strftime('%Y-%m-%d %HUTC')} is a COL"
            )

        object_slice = object_slice.astype(int)
        object_slice[object_slice > 0] = iobj + 1
        object_slice = (
            object_slice + col_objects[time_start:time_stop, lat_start:lat_stop, lon_start:lon_stop]
        )
        col_objects[time_start:time_stop, lat_start:lat_stop, lon_start:lon_stop] = object_slice

    # Number of col_objects identified
    col_objects_ids = np.unique(col_objects)
    col_objects_ids = col_objects_ids[col_objects_ids > 0]
    logger.debug(f"{Fore.GREEN} {col_objects_ids.size} Cut-off lows found")

    if nc_file is not None:
        logger.debug(f"{Style.BRIGHT} Save objects into a netCDF")

        fino = xr.Dataset(
            {
                "cy_z500_objects": (["time", "latitude", "longitude"], cy_z500_objects),
                "col_objects": (["time", "latitude", "longitude"], col_objects),
                "front_objects": (["time", "latitude", "longitude"], front_objects),
                "z500": (["time", "latitude", "longitude"], z500_data),
                "u200": (["time", "latitude", "longitude"], u200_data),
                "t850": (["time", "latitude", "longitude"], t850_data),
                "u850": (["time", "latitude", "longitude"], u850_data),
                "v850": (["time", "latitude", "longitude"], v850_data),
                "pr": (["time", "latitude", "longitude"], pr_data),
                "pr_max": (["time", "latitude", "longitude"], pr_data_max),
            },
            coords={
                "time": times.values,
                "latitude": Lat[:, 0].squeeze(),
                "longitude": Lon[0, :].squeeze(),
            },
        )

        # Adding units to 'pr' and 'pr_max'
        fino["pr"].attrs["units"] = "mm"
        fino["pr_max"].attrs["units"] = "mm/hr"

        # Optionally, you can add a description or other metadata
        fino["pr"].attrs["description"] = "Accumulated Precipitation"
        fino["pr_max"].attrs["description"] = "Maximum precipitation rate"

        reference_date = np.datetime64("1940-01-01T00:00:00")
        fino["time"] = (fino["time"] - reference_date) / np.timedelta64(1, "h")
        fino["time"].attrs["units"] = f"hours since {reference_date}"
        fino["time"].attrs["calendar"] = "standard"

        fino.to_netcdf(
            nc_file,
            mode="w",
            format="NETCDF4",
            encoding={
                "z500": {"zlib": True, "complevel": 5},
                "u200": {"zlib": True, "complevel": 5},
                "cy_z500_objects": {"zlib": True, "complevel": 5},
                "col_objects": {"zlib": True, "complevel": 5},
            },
        )

        end_time = time.time()
        logger.debug(
            f"{Style.BRIGHT} ======> 'Writing files: {(end_time - start_time):.2f} seconds \n"
        )
        start_time = time.time()

    else:
        logger.debug(f"{Fore.YELLOW}No writing files required, output file name is empty")

    return col_objects
