#!/usr/bin/env python

"""
   tracking_functions.py

   This file contains the tracking fuctions for the object
   identification and tracking of precipitation areas, cyclones,
   clouds, and moisture streams

"""
import glob
import os
from pdb import set_trace as stop
import pickle
from itertools import groupby
import datetime
import time
import logging


import numpy as np
import matplotlib.path as mplPath
import netCDF4 as nc
import pandas as pd
import xarray as xr

import metpy.calc as calc

from scipy.ndimage import filters, distance_transform_edt
from scipy.ndimage import morphology
from scipy import ndimage
from scipy.spatial.distance import cdist


from constants import const
import atmotrack_config as cfg

from colorama import Fore, Style, init
init(autoreset=True)


###########################################################
###########################################################

### UTILITY Functions
def calc_grid_distance_area(lat,lon):
    """ Function to calculate grid parameters
        It uses haversine function to approximate distances
        It approximates the first row and column to the sencond
        because coordinates of grid cell center are assumed
        lat, lon: input coordinates(degrees) 2D [y,x] dimensions
        dx: distance (m)
        dy: distance (m)
        area: area of grid cell (m2)
        grid_distance: average grid distance over the domain (m)
    """
    dy = np.zeros(lat.shape)
    dx = np.zeros(lon.shape)

    dx[:,1:]=haversine(lat[:,1:],lon[:,1:],lat[:,:-1],lon[:,:-1])
    dy[1:,:]=haversine(lat[1:,:],lon[1:,:],lat[:-1,:],lon[:-1,:])

    dx[:,0] = dx[:,1]
    dy[0,:] = dy[1,:]

    area = dx*dy
    grid_distance = np.mean(np.append(dy[:, :, None], dx[:, :, None], axis=2))

    return dx,dy,area,grid_distance

def haversine(lat1, lon1, lat2, lon2):

    """Function to calculate grid distances lat-lon
       This uses the Haversine formula
       lat,lon : input coordinates (degrees) - array or float
       dist_m : distance (m)
       https://en.wikipedia.org/wiki/Haversine_formula
       """
    # convert decimal degrees to radians
    lon1 = np.radians(lon1)
    lon2 = np.radians(lon2)
    lat1 = np.radians(lat1)
    lat2 = np.radians(lat2)

    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat / 2) ** 2 + \
    np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    c = 2 * np.arcsin(np.sqrt(a))
    # Radius of earth in kilometers is 6371
    dist_m = c * const.earth_radius
    return dist_m

def calculate_area_objects(objects_id_pr,object_indices,grid_cell_area):

    """ Calculates the area of each object during their lifetime
        one area value for each object and each timestep it exist
    """
    num_objects = len(object_indices)
    area_objects = np.array(
        [
            [
            np.sum(grid_cell_area[object_indices[obj][1:]][objects_id_pr[object_indices[obj]][tstep, :, :] == obj + 1])
            for tstep in range(objects_id_pr[object_indices[obj]].shape[0])
            ]
        for obj in range(num_objects)
        ],
    dtype=object
    )

    return area_objects

def remove_small_short_objects(objects_id,area_objects,min_area,min_time,DT):
    """Checks if the object is large enough during enough time steps
        and removes objects that do not meet this condition
        area_object: array of lists with areas of each objects during their lifetime [objects[tsteps]]
        min_area: minimum area of the object (km2)
        min_time: minimum time with the object large enough (hours)
    """

    #create final object array
    sel_objects = np.zeros(objects_id.shape,dtype=int)

    new_obj_id = 1
    for obj,_ in enumerate(area_objects):
        AreaTest = np.max(
            np.convolve(
                np.array(area_objects[obj]) >= min_area * 1000**2,
                np.ones(int(min_time/ DT)),
                mode="valid",
            )
        )

        if (AreaTest == int(min_time/ DT)) & (
            len(area_objects[obj]) >= int(min_time/ DT)
        ):
            sel_objects[objects_id == (obj + 1)] =     new_obj_id
            new_obj_id += 1

    return sel_objects

def relabel_to_consecutive(labels):
    unique_labels = np.unique(labels)  # Get unique labels
    new_labels = np.zeros_like(labels) # Initialize array with the same shape as input
    mapping = {old_label: new_label for new_label, old_label in enumerate(unique_labels)}

    for old_label, new_label in mapping.items():
        new_labels[labels == old_label] = new_label

    return new_labels

###########################################################
###########################################################
def calc_object_characteristics(
    var_objects,  # feature object file
    var_data,  # original file used for feature detection
    filename_out,  # output file name and locaiton
    times,  # timesteps of the data
    Lat,  # 2D latidudes
    Lon,  # 2D Longitudes
    grid_spacing,  # average grid spacing
    grid_cell_area,
    min_tsteps=1  # minimum lifetime in data timesteps
    ):
    # ========

    num_objects = int(var_objects.max())
    object_indices = ndimage.find_objects(var_objects)

    if num_objects >= 1:
        objects_charac = {}
        logging.debug("            Loop over " + str(num_objects) + " objects")
        for iobj in range(num_objects):

            object_slice = np.copy(var_objects[object_indices[iobj]])
            data_slice   = np.copy(var_data[object_indices[iobj]])
            time_idx_slice = object_indices[iobj][0]
            lat_idx_slice  = object_indices[iobj][1]
            lon_idx_slice  = object_indices[iobj][2]

            #if len(object_slice) >= min_tsteps:
            if np.sum(np.any(object_slice==iobj+1,axis=(1,2))) >= min_tsteps:

                data_slice[object_slice!=(iobj + 1)] = np.nan
                grid_cell_area_slice = np.tile(grid_cell_area[lat_idx_slice, lon_idx_slice], (len(data_slice), 1, 1))
                grid_cell_area_slice[object_slice != (iobj + 1)] = np.nan
                lat_slice = Lat[lat_idx_slice, lon_idx_slice]
                lon_slice = Lon[lat_idx_slice, lon_idx_slice]


                # calculate statistics
                obj_times = times[time_idx_slice]
                obj_size  = np.nansum(grid_cell_area_slice, axis=(1, 2))
                obj_min = np.nanmin(data_slice, axis=(1, 2))
                obj_max = np.nanmax(data_slice, axis=(1, 2))
                obj_mean = np.nanmean(data_slice, axis=(1, 2))
                obj_tot = np.nansum(data_slice, axis=(1, 2))


                # Track lat/lon
                obj_mass_center = \
                np.array([ndimage.measurements.center_of_mass(object_slice[tt,:,:]==(iobj+1)) for tt in range(object_slice.shape[0])])

                if np.any(np.isnan(obj_mass_center)):
                    raise ValueError("mass center array contains NaNs")

                obj_track = np.full([len(obj_mass_center), 2], np.nan)

                obj_track[:,0]=np.array([lat_slice[int(round(obj_loc[0])),int(round(obj_loc[1]))]    for tstep, obj_loc in enumerate(obj_mass_center)])
                obj_track[:,1]=np.array([lon_slice[int(round(obj_loc[0])),int(round(obj_loc[1]))]    for tstep, obj_loc in enumerate(obj_mass_center)])

                if np.any(np.isnan(obj_track)):
                    raise ValueError("track array contains NaNs")

                obj_speed = (np.sum(np.diff(obj_mass_center,axis=0)**2,axis=1)**0.5) * (grid_spacing / 1000.0)

                this_object_charac = {
                    "mass_center_loc": obj_mass_center,
                    "speed": obj_speed,
                    "tot": obj_tot,
                    "min": obj_min,
                    "max": obj_max,
                    "mean": obj_mean,
                    "size": obj_size,
                    #                        'rgrAccumulation':rgrAccumulation,
                    "times": obj_times,
                    "track": obj_track,
                }

                try:
                    objects_charac[str(iobj + 1)] = this_object_charac
                except:
                    raise ValueError ("Error asigning properties to final dictionary")


        if filename_out is not None:
            with open(filename_out, 'wb') as handle:
                pickle.dump(objects_charac, handle)

        return objects_charac


# ==============================================================
# ==============================================================

def ConnectLon(object_indices):
    for tt in range(object_indices.shape[0]):
        EDGE = np.append(
            object_indices[tt, :, -1][:, None], object_indices[tt, :, 0][:, None], axis=1
        )
        iEDGE = np.sum(EDGE > 0, axis=1) == 2
        OBJ_Left = EDGE[iEDGE, 0]
        OBJ_Right = EDGE[iEDGE, 1]
        OBJ_joint = np.array(
            [
                OBJ_Left[ii].astype(str) + "_" + OBJ_Right[ii].astype(str)
                for ii,_ in enumerate(OBJ_Left)
            ]
        )
        NotSame = OBJ_Left != OBJ_Right
        OBJ_joint = OBJ_joint[NotSame]
        OBJ_unique = np.unique(OBJ_joint)
        # set the eastern object to the number of the western object in all timesteps
        for obj,_ in enumerate(OBJ_unique):
            ObE = int(OBJ_unique[obj].split("_")[1])
            ObW = int(OBJ_unique[obj].split("_")[0])
            object_indices[object_indices == ObE] = ObW
    return object_indices



### Break up long living cyclones by extracting the biggest cyclone at each time
def BreakupObjects(
    DATA,  # 3D matrix [time,lat,lon] containing the objects
    min_tsteps,  # minimum lifetime in data timesteps
    dT,
):  # time step in hours

    object_indices = ndimage.find_objects(DATA)
    MaxOb = np.max(DATA)
    MinLif = int(24 / dT)  # min lifetime of object to be split
    AVmax = 1.5

    obj_structure_2D = np.zeros((3, 3, 3))
    obj_structure_2D[1, :, :] = 1
    rgiObjects2D, nr_objects2D = ndimage.label(DATA, structure=obj_structure_2D)

    rgiObjNrs = np.unique(DATA)[1:]
    TT = np.array([object_indices[obj][0].stop - object_indices[obj][0].start for obj in range(MaxOb)])
    # Sel_Obj = rgiObjNrs[TT > MinLif]

    # Average 2D objects in 3D objects?
    Av_2Dob = np.zeros((len(rgiObjNrs)))
    Av_2Dob[:] = np.nan
    ii = 1
    for obj,_ in enumerate(rgiObjNrs):
        #         if TT[obj] <= MinLif:
        #             # ignore short lived objects
        #             continue
        SelOb = rgiObjNrs[obj] - 1
        DATA_ACT = np.copy(DATA[object_indices[SelOb]])
        iOb = rgiObjNrs[obj]
        rgiObjects2D_ACT = np.copy(rgiObjects2D[object_indices[SelOb]])
        rgiObjects2D_ACT[DATA_ACT != iOb] = 0

        Av_2Dob[obj] = np.mean(
            np.array(
                [
                    len(np.unique(rgiObjects2D_ACT[tt, :, :])) - 1
                    for tt in range(DATA_ACT.shape[0])
                ]
            )
        )
        if Av_2Dob[obj] > AVmax:
            ObjectArray_ACT = np.copy(DATA_ACT)
            ObjectArray_ACT[:] = 0
            rgiObAct = np.unique(rgiObjects2D_ACT[0, :, :])[1:]
            for tt in range(1, rgiObjects2D_ACT[:, :, :].shape[0]):
                rgiObActCP = list(np.copy(rgiObAct))
                for ob1 in rgiObAct:
                    tt1_obj = list(
                        np.unique(
                            rgiObjects2D_ACT[tt, rgiObjects2D_ACT[tt - 1, :] == ob1]
                        )[1:]
                    )
                    if len(tt1_obj) == 0:
                        # this object ends here
                        rgiObActCP.remove(ob1)
                        continue
                    elif len(tt1_obj) == 1:
                        rgiObjects2D_ACT[
                            tt, rgiObjects2D_ACT[tt, :] == tt1_obj[0]
                        ] = ob1
                    else:
                        VOL = [
                            np.sum(rgiObjects2D_ACT[tt, :] == tt1_obj[jj])
                            for jj,_ in enumerate(tt1_obj)
                        ]
                        rgiObjects2D_ACT[
                            tt, rgiObjects2D_ACT[tt, :] == tt1_obj[np.argmax(VOL)]
                        ] = ob1
                        tt1_obj.remove(tt1_obj[np.argmax(VOL)])
                        rgiObActCP = rgiObActCP + list(tt1_obj)

                # make sure that mergers are assigned the largest object
                for ob2 in rgiObActCP:
                    ttm1_obj = list(
                        np.unique(
                            rgiObjects2D_ACT[tt - 1, rgiObjects2D_ACT[tt, :] == ob2]
                        )[1:]
                    )
                    if len(ttm1_obj) > 1:
                        VOL = [
                            np.sum(rgiObjects2D_ACT[tt - 1, :] == ttm1_obj[jj])
                            for jj,_ in enumerate(ttm1_obj)
                        ]
                        rgiObjects2D_ACT[tt, rgiObjects2D_ACT[tt, :] == ob2] = ttm1_obj[
                            np.argmax(VOL)
                        ]

                # are there new object?
                NewObj = np.unique(rgiObjects2D_ACT[tt, :, :])[1:]
                NewObj = list(np.setdiff1d(NewObj, rgiObAct))
                if len(NewObj) != 0:
                    rgiObActCP = rgiObActCP + NewObj
                rgiObActCP = np.unique(rgiObActCP)
                rgiObAct = np.copy(rgiObActCP)

            rgiObjects2D_ACT[rgiObjects2D_ACT != 0] = np.copy(
                rgiObjects2D_ACT[rgiObjects2D_ACT != 0] + MaxOb
            )
            MaxOb = np.max(DATA)

            # save the new objects to the original object array
            TMP = np.copy(DATA[object_indices[SelOb]])
            TMP[rgiObjects2D_ACT != 0] = rgiObjects2D_ACT[rgiObjects2D_ACT != 0]
            DATA[object_indices[SelOb]] = np.copy(TMP)

    # clean up object matrix
    Unique = np.unique(DATA)[1:]
    object_indices = ndimage.find_objects(DATA)
    rgiVolObj = np.array(
        [
            np.sum(DATA[object_indices[Unique[obj] - 1]] == Unique[obj])
            for obj,_ in enumerate(Unique)
        ]
    )
    TT = np.array(
        [
            object_indices[Unique[obj] - 1][0].stop - object_indices[Unique[obj] - 1][0].start
            for obj,_ in enumerate(Unique)
        ]
    )

    # create final object array
    CY_objectsTMP = np.copy(DATA)
    CY_objectsTMP[:] = 0
    ii = 1
    for obj,_ in enumerate(rgiVolObj):
        if TT[obj] >= min_tsteps / dT:
            CY_objectsTMP[DATA == Unique[obj]] = ii
            ii = ii + 1

    # lable the objects from 1 to N
    DATA_fin = np.copy(CY_objectsTMP)
    DATA_fin[:] = 0
    Unique = np.unique(CY_objectsTMP)[1:]
    ii = 1
    for obj,_ in enumerate(Unique):
        DATA_fin[CY_objectsTMP == Unique[obj]] = ii
        ii = ii + 1

    return DATA_fin



def clean_up_objects(DATA,
                     dT,
                     min_tsteps = 0,
                     obj_splitmerge = None):
    """ Function to remove objects that are too short lived
        and to numerrate the object from 1...N
    """
    
    object_indices = ndimage.find_objects(DATA)
    MaxOb = np.max(DATA)
    MinLif = int(24 / dT)  # min lifetime of object to be split
    AVmax = 1.5

    id_translate = np.zeros((len(object_indices),2))
    objectsTMP = np.copy(DATA)
    objectsTMP[:] = 0
    ii = 1
    for obj in range(len(object_indices)):
        if object_indices[obj] != None:
            if object_indices[obj][0].stop - object_indices[obj][0].start >= min_tsteps / dT:
                Obj_tmp = np.copy(objectsTMP[object_indices[obj]])
                Obj_tmp[DATA[object_indices[obj]] == obj+1] = ii
                objectsTMP[object_indices[obj]] = Obj_tmp
                id_translate[obj,0] = obj+1
                id_translate[obj,1] = ii
                ii = ii + 1
            else:
                id_translate[obj,0] = obj+1
                id_translate[obj,1] = -1
        else:
            id_translate[obj,0] = obj+1
            id_translate[obj,1] = -1

    # adjust the directory strucutre accordingly
    obj_splitmerge_clean = {}

    if obj_splitmerge != None:
        id_translate = id_translate.astype(int)  
        keys = np.copy(list(obj_splitmerge.keys()))
        for jj in range(len(keys)):
            obj_loc = np.where(int(list(keys)[jj]) == id_translate[:,0])[0][0]
            if id_translate[obj_loc,1] == -1:
                del obj_splitmerge[list(keys)[jj]]

        # loop over objects and relable their indices if nescessary
        obj_splitmerge_clean = {}
        keys = np.copy(list(obj_splitmerge.keys()))
        core_translate = np.isin(id_translate[:,0], keys.astype(int))
        id_translate = id_translate[core_translate,:]
        for jj in range(len(keys)):
            obj_loc = np.where(int(list(keys)[jj]) == id_translate[:,0])[0][0]
            mergsplit = np.array(obj_splitmerge[keys[jj]])
            for kk in range(id_translate.shape[0]):
                mergsplit[np.isin(mergsplit, id_translate[kk,0])] = id_translate[kk,1]
            obj_splitmerge_clean[str(int(id_translate[obj_loc,1]))] = mergsplit
        
    return objectsTMP, obj_splitmerge_clean

#####################################################################
#####################################################################


def split_objects(old_objects, distance_threshold=10):
    # Make a copy of old_objects to store the new labels
    new_objects = np.copy(old_objects)

    # Track labels that need to be reassigned across timesteps
    last_labels_positions = {}

    # Iterate over each time step
    for t in range(old_objects.shape[0]):
        # Get the labeled objects for the current time slice
        slice_data = old_objects[t]
        unique_labels = np.unique(slice_data[slice_data > 0])  # Ignore background (label 0)

        current_labels_positions = {}

        for label_id in unique_labels:
            # Create a mask for the current object
            mask = slice_data == label_id

            # Label disconnected regions within this object
            relabeled, num_features = ndimage.label(mask)

            # If there are multiple disconnected regions
            if num_features > 1:
                # Compute distance transform within the original mask
                distance_map = distance_transform_edt(mask)

                # Measure the centroid of each feature within the object
                regions = [np.argwhere(relabeled == i) for i in range(1, num_features + 1)]
                
                for i, region_i in enumerate(regions):
                    for j, region_j in enumerate(regions[i + 1:], start=i + 1):
                        # Calculate the maximum distance in the distance map for each region
                        max_dist_i = distance_map[tuple(region_i.T)].max()
                        max_dist_j = distance_map[tuple(region_j.T)].max()

                        # Check if the maximum distance between these regions exceeds the threshold
                        distance = np.linalg.norm(region_i.mean(axis=0) - region_j.mean(axis=0))
                        if distance > distance_threshold or (max_dist_i > distance_threshold and max_dist_j > distance_threshold):
                            new_label = np.max(new_objects) + 1
                            new_objects[t][relabeled == j + 1] = new_label
                            current_labels_positions[new_label] = region_j.mean(axis=0)
                            current_labels_positions[label_id] = region_i.mean(axis=0)
            else:
                # Only one connected region, store its centroid
                current_labels_positions[label_id] = np.argwhere(mask).mean(axis=0)

        # Prepare a separate dictionary for relabeling
        relabel_updates = {}

        # Check if any label should be reassigned based on proximity to previous timestep labels
        if t > 0:
            for current_label, current_pos in current_labels_positions.items():
                # Compare each current position with previous labels' positions
                closest_prev_label, closest_prev_pos = min(
                    last_labels_positions.items(),
                    key=lambda item: np.linalg.norm(item[1] - current_pos),
                    default=(None, None)
                )
                # Relabel if the closest previous label is within the distance threshold
                if closest_prev_label is not None and np.linalg.norm(closest_prev_pos - current_pos) < distance_threshold:
                    relabel_updates[current_label] = closest_prev_label

        # Apply relabel updates after iteration to avoid modifying during the loop
        for current_label, new_label in relabel_updates.items():
            new_objects[t][new_objects[t] == current_label] = new_label
            current_labels_positions[new_label] = current_labels_positions.pop(current_label)  # Update position with new label

        # Update last positions for the next timestep
        last_labels_positions = current_labels_positions

    return new_objects
#####################################################################
#####################################################################




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
    
    #Reading tracking parameters
    
    DT = cfg.DT
    front_treshold = cfg.front_treshold
    MinAreaFR = cfg.MinAreaFR 
    
    # Calculate the horizontal derivatives of u and v
    
    dx,dy,_,_ = calc_grid_distance_area(Lat,Lon)
    
    du = np.gradient(np.array(u850))
    dv = np.gradient(np.array(v850))
    
    # Calculate potential vorticity term for frontal detection
    PV = np.abs(dv[-1] / dx[None, :] - du[-2] / dy[None, :])
    
    # Calculate the temperature gradient magnitude
    vgrad = np.gradient(np.array(t850), axis=(1, 2))
    Tgrad = np.sqrt(vgrad[0]**2 + vgrad[1]**2)
    
    # Calculate Fstar, the frontal diagnostic variable
    Fstar = PV * Tgrad
    
    # Set a threshold for temperature gradient (optional, based on literature)
    Tgrad_zero = 0.45  # Assumed threshold in K/(100 km)
    
    # Calculate the Coriolis parameter
    CoriolisPar = np.array(calc.coriolis_parameter(np.deg2rad(Lat)))
    
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
    Fmask = (Frontal_Diagnostic > front_treshold)
    
    # Label connected regions that exceed the threshold
    rgiObjectsUD, nr_objectsUD = ndimage.label(Fmask, structure=rgiObj_Struct_Fronts)
    logging.debug(f'{Fore.GREEN}  {str(nr_objectsUD)} object(s) found')
    
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
        area_slice =  Area[Objects[ob]]
        object_slice = rgiObjectsUD[Objects[ob]]
                
        # Calculate the area of the current object where the label matches (ob + 1)
        try:
            object_area = np.sum(area_slice[object_slice == ob + 1])
        except:
            import pdb; pdb.set_trace()  # fmt: skip
        
        # Append the computed area to the list
        rgiAreaObj.append(object_area)

    # Convert the list to a NumPy array for further processing
    rgiAreaObj = np.array(rgiAreaObj)
    
    # rgiAreaObj = np.array([
    #     np.sum(Area[Objects[ob][1:]][rgiObjectsUD[Objects[ob]][0, :, :] == ob + 1]) 
    #     for ob in range(nr_objectsUD)
    # ])
    
    # Create the final object array, excluding small objects
    FR_objects = np.copy(rgiObjectsUD)
    TooSmall = np.where(rgiAreaObj < MinAreaFR * 1000**2)  # Convert MinAreaFR to square meters
    FR_objects[np.isin(FR_objects, TooSmall[0] + 1)] = 0
    
    return FR_objects

    

############################################################
###########################################################
#### ======================================================
# function to perform MCS tracking
def MCS_tracking(
    pr_data,
    bt_data,
    times,
    Lon,
    Lat,
    nc_file
):
    """ Function to track MCS from precipitation and brightness temperature
    """

    start_time = time.time()
    #Reading tracking parameters

    DT = cfg.DT

    #Precipitation tracking setup
    smooth_sigma_pr = cfg.smooth_sigma_pr   # [0] Gaussion std for precipitation smoothing
    thres_pr        = cfg.thres_pr     # [2] precipitation threshold [mm/h]
    min_time_pr     = cfg.min_time_pr     # [3] minum lifetime of PR feature in hours
    min_area_pr     = cfg.min_area_pr      # [5000] minimum area of precipitation feature in km2
    # Brightness temperature (Tb) tracking setup
    smooth_sigma_bt = cfg.smooth_sigma_bt   #  [0] Gaussion std for Tb smoothing
    thres_bt        = cfg.thres_bt     # [241] minimum Tb of cloud shield
    min_time_bt     = cfg.min_time_bt       # [9] minium lifetime of cloud shield in hours
    min_area_bt     = cfg.min_area_bt       # [40000] minimum area of cloud shield in km2
    # MCs detection
    MCS_min_area      = cfg.MCS_min_area    # [5000] km2
    MCS_thres_pr       = cfg.MCS_thres_pr      # [10] minimum max precipitation in mm/h
    MCS_thres_peak_pr   = cfg.MCS_thres_peak_pr  # [10] Minimum lifetime peak of MCS precipitation
    MCS_thres_bt     = cfg.MCS_thres_bt        # [225] minimum brightness temperature
    MCS_min_area_bt         = cfg.MCS_min_area_bt        # [40000] min cloud area size in km2
    MCS_min_time     = cfg.MCS_min_time    # [4] minimum time step

    #Calculating grid distances and areas

    _,_,grid_cell_area,grid_spacing = calc_grid_distance_area(Lat,Lon)
    grid_cell_area[grid_cell_area < 0] = 0

    obj_structure_3D = np.ones((3,3,3))

    start_day = times[0]


    # connect over date line?
    crosses_dateline = False
    if (Lon[0, 0] < -176) & (Lon[0, -1] > 176):
        crosses_dateline = True

    end_time = time.time()
    logging.debug(f"======> 'Initialize MCS tracking function: {(end_time-start_time):.2f} seconds \n")
    start_time = time.time()
    # --------------------------------------------------------
    # TRACKING PRECIP OBJECTS
    # --------------------------------------------------------
    logging.debug("        track  precipitation")

    pr_smooth= filters.gaussian_filter(
        pr_data, sigma=(0, smooth_sigma_pr, smooth_sigma_pr)
    )
    pr_mask = pr_smooth >= thres_pr * DT
    objects_id_pr, num_objects = ndimage.label(pr_mask, structure=obj_structure_3D)
    logging.debug("            " + str(num_objects) + " precipitation object found")

    # connect objects over date line
    if crosses_dateline:
        objects_id_pr = ConnectLon(objects_id_pr)

    # get indices of object to reduce memory requirements during manipulation
    object_indices = ndimage.find_objects(objects_id_pr)


    #Calcualte area of objects
    area_objects = calculate_area_objects(objects_id_pr,object_indices,grid_cell_area)

    # Keep only large and long enough objects
    # Remove objects that are too small or short lived
    pr_objects = remove_small_short_objects(objects_id_pr,area_objects,min_area_pr,min_time_pr,DT)

    grPRs = calc_object_characteristics(
        pr_objects,  # feature object file
        pr_data,  # original file used for feature detection
        f"{cfg.path_in}/PR_{start_day.year}{start_day.month:02d}",
        times,  # timesteps of the data
        Lat,  # 2D latidudes
        Lon,  # 2D Longitudes
        grid_spacing,
        grid_cell_area,
        min_tsteps=int(min_time_pr/ DT), # minimum lifetime in data timesteps
    )

    end_time = time.time()
    logging.debug(f"======> 'Tracking precip: {(end_time-start_time):.2f} seconds \n")
    start_time = time.time()
    # --------------------------------------------------------
    # TRACKING CLOUD (BT) OBJECTS
    # --------------------------------------------------------
    logging.debug("            track  clouds")
    bt_smooth = filters.gaussian_filter(
        bt_data, sigma=(0, smooth_sigma_bt, smooth_sigma_bt)
    )
    bt_mask = bt_smooth <= thres_bt
    objects_id_bt, num_objects = ndimage.label(bt_mask, structure=obj_structure_3D)
    logging.debug("            " + str(num_objects) + " cloud object found")

    # connect objects over date line
    if crosses_dateline:
        logging.debug("            connect cloud objects over date line")
        objects_id_bt = ConnectLon(objects_id_bt)

    # get indices of object to reduce memory requirements during manipulation
    object_indices = ndimage.find_objects(objects_id_bt)

    #Calcualte area of objects
    area_objects = calculate_area_objects(objects_id_bt,object_indices,grid_cell_area)

    # Keep only large and long enough objects
    # Remove objects that are too small or short lived
    bt_objects = remove_small_short_objects(objects_id_bt,area_objects,min_area_bt,min_time_bt,DT)

    end_time = time.time()
    logging.debug(f"======> 'Tracking clouds: {(end_time-start_time):.2f} seconds \n")
    start_time = time.time()

    #logging.debug("            break up long living cloud shield objects that heve many elements")
    #bt_objects = BreakupObjects(bt_objects, int(min_time_bt / DT), DT)

    end_time = time.time()
    logging.debug(f"======> 'Breaking up cloud objects: {(end_time-start_time):.2f} seconds \n")
    start_time = time.time()

    grCs = calc_object_characteristics(
        bt_objects,  # feature object file
        bt_data,  # original file used for feature detection
        f"{cfg.path_in}/BT_{start_day.year}{start_day.month:02d}",
        times,  # timesteps of the data
        Lat,  # 2D latidudes
        Lon,  # 2D Longitudes
        grid_spacing,
        grid_cell_area,
        min_tsteps=int(min_time_bt / DT), # minimum lifetime in data timesteps
    )
    end_time = time.time()
    logging.debug(f"======> 'Calculate cloud characteristics: {(end_time-start_time):.2f} seconds \n")
    start_time = time.time()
    # --------------------------------------------------------
    # CHECK IF PR OBJECTS QUALIFY AS MCS
    # (or selected strom type according to msc_config.py)
    # --------------------------------------------------------
    logging.debug("            check if pr objects quallify as MCS (or selected storm type)")
    # check if precipitation object is from an MCS
    object_indices = ndimage.find_objects(pr_objects)
    MCS_objects = np.zeros(pr_objects.shape,dtype=int)

    for iobj,_ in enumerate(object_indices):

        if object_indices[iobj] is None:
            continue

        time_slice = object_indices[iobj][0]
        lat_slice  = object_indices[iobj][1]
        lon_slice  = object_indices[iobj][2]


        pr_object_slice= pr_objects[object_indices[iobj]]
        pr_object_act = np.where(pr_object_slice==iobj+1,True,False)

        if len(pr_object_act) < 2:
            continue

        pr_slice =  pr_data[object_indices[iobj]]
        pr_act = np.copy(pr_slice)
        pr_act[~pr_object_act] = 0

        bt_slice  = bt_data[object_indices[iobj]]
        bt_act = np.copy(bt_slice)
        bt_act[~pr_object_act] = 0

        bt_object_slice = bt_objects[object_indices[iobj]]
        bt_object_act = np.copy(bt_object_slice)
        bt_object_act[~pr_object_act] = 0

        area_act = np.tile(grid_cell_area[lat_slice, lon_slice], (pr_act.shape[0], 1, 1))
        area_act[~pr_object_act] = 0



        pr_size = np.array(np.sum(area_act,axis=(1,2)))
        pr_max = np.array(np.max(pr_act,axis=(1,2)))


        #Check overlaps between clouds (bt) and precip objects
        objects_overlap = np.delete(np.unique(bt_object_act[pr_object_act]),0)

        if len(objects_overlap) == 0:
            # no deep cloud shield is over the precipitation
            continue

        ## Keep bt objects (entire) that partially overlap with pr object

        bt_object_overlap = np.in1d(bt_objects[time_slice].flatten(), objects_overlap).reshape(bt_objects[time_slice].shape)

        # Get size of all cloud (bt) objects together
        # We get size of all cloud objects that overlap partially with pr object
        # DO WE REALLY NEED THIS?

        bt_size = np.array(
            [
            np.sum(grid_cell_area[bt_object_overlap[tt, :, :] > 0])
            for tt in range(bt_object_overlap.shape[0])
            ]
        )

        #Check if BT is below threshold over precip areas
        bt_min_temp = np.nanmin(np.where(bt_object_slice>0,bt_slice,999),axis=(1,2))



        # minimum lifetime peak precipitation
        is_pr_peak_intense = np.max(pr_max) >= MCS_thres_peak_pr * DT
        MCS_test = (
            (bt_size / 1000**2 >= MCS_min_area_bt)
            & (bt_min_temp  <= MCS_thres_bt )
            & (pr_size / 1000**2 >= MCS_min_area )
            & (pr_max >= MCS_thres_pr * DT)
            & (is_pr_peak_intense)
        )

        # assign unique object numbers

        pr_object_act = np.array(pr_object_act).astype(int)
        pr_object_act[pr_object_act == 1] = iobj + 1

        window_length = int(MCS_min_time / DT)
        moving_averages = np.convolve(MCS_test, np.ones(window_length), 'valid') / window_length
        if (len(moving_averages) > 0) & (np.max(moving_averages) == 1):
            TMP = np.copy(MCS_objects[object_indices[iobj]])
            TMP = TMP + pr_object_act
            MCS_objects[object_indices[iobj]] = TMP

        else:
            continue

    #if len(objects_overlap)>1: import pdb; pdb.set_trace()
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
        min_tsteps=int(MCS_min_time / DT), # minimum lifetime in data timesteps
    )

    end_time = time.time()
    logging.debug(f"======> 'MCS tracking: {(end_time-start_time):.2f} seconds \n")
    start_time = time.time()


    ###########################################################
    ###########################################################
    ## WRite netCDF with xarray
    if nc_file is not None:
        logging.debug ('Save objects into a netCDF')

        fino=xr.Dataset({'MCS_objects':(['time','y','x'],objects_id_MCS),
                         'PR':(['time','y','x'],pr_data),
                         'PR_objects':(['time','y','x'],objects_id_pr),
                         'BT':(['time','y','x'],bt_data),
                         'BT_objects':(['time','y','x'],objects_id_bt),
                         'lat':(['y','x'],Lat),
                         'lon':(['y','x'],Lon)},
                         coords={'time':times.values})

        fino.to_netcdf(nc_file,mode='w',encoding={'PR':{'zlib': True,'complevel': 5},
                                                 'PR_objects':{'zlib': True,'complevel': 5},
                                                 'BT':{'zlib': True,'complevel': 5},
                                                 'BT_objects':{'zlib': True,'complevel': 5},
                                                 'MCS_objects':{'zlib': True,'complevel': 5}})

        end_time = time.time()
        logging.debug(f"======> 'Writing files: {(end_time-start_time):.2f} seconds \n")
        start_time = time.time()
    else:
        logging.debug(f"No writing files required, output file name is empty")
    ###########################################################
    ###########################################################
    # ============================
    # Write NetCDF
    return grMCSs, MCS_objects



#####################################################################
#####################################################################

def CY_ACY_z500_tracking(
    z500_data,
    times,
    Lon,
    Lat,
    nc_file = None
):
    
    start_time = time.time()

    #Reading tracking parameters
    #Time step in hours
    DT = cfg.DT

    z500_low_anom = cfg.z500_smooth_low_anom
    z500_high_anom = cfg.z500_smooth_high_anom
    MinTimeCY = cfg.MinTimeCY
    MinTimeACY = cfg.MinTimeACY



    #Calculating grid distances and areas
    _,_,grid_cell_area,grid_spacing = calc_grid_distance_area(Lat,Lon)
    grid_cell_area[grid_cell_area < 0] = 0

    obj_structure_3D = np.ones((3,3,3))

    start_day = times[0]

    # connect over date line?
    crosses_dateline = False
    if (Lon[0, 0] < -176) & (Lon[0, -1] > 176):
        crosses_dateline = True

    end_time = time.time()
    logging.debug(f"{Fore.MAGENTA}======> 'Initialize z500 Cyclone/Anticyclone tracking function: {(end_time-start_time):.2f} seconds \n")
    start_time = time.time()

    # --------------------------------------------------------
    # TRACKING z500 anomaly OBJECTS
    # --------------------------------------------------------
    logging.debug(f"{Style.BRIGHT} Tracking 500 hPa cyclones and anticyclones")

    #Divide by gravity to get geopotential height
    z500 = z500_data / const.g

    z500_smooth = filters.uniform_filter(z500, size=(1, int(100/(grid_spacing/1000.)), int(100/(grid_spacing/1000.))))

    z500_smooth_mean = filters.uniform_filter(z500, size=(int(78/DT), int(3000/(grid_spacing/1000.)), int(3000/(grid_spacing/1000.))))

    z500_smooth_anom = z500_smooth - z500_smooth_mean

    z_low = z500_smooth_anom < z500_low_anom
    z_high = z500_smooth_anom > z500_high_anom
    


    objects_id_z500_low, low_num_objects = ndimage.label(z_low, structure=obj_structure_3D)
    logging.debug(f"{Fore.GREEN} {low_num_objects} cyclones found")

    objects_id_z500_high, hi_num_objects = ndimage.label(z_high, structure=obj_structure_3D)
    logging.debug(f"{Fore.GREEN} {hi_num_objects} anticyclones found")

    # connect objects over date line
    if crosses_dateline:
        objects_id_z500_low = ConnectLon(objects_id_z500_low)
        objects_id_z500_high = ConnectLon(objects_id_z500_high)
      
    # # get indices of object to reduce memory requirements during manipulation
    # object_indices_low = ndimage.find_objects(objects_id_z500_low)
    # object_indices_high = ndimage.find_objects(objects_id_z500_high)


    #Clean up objects
    cy_z500_objects, _ = clean_up_objects(objects_id_z500_low,min_tsteps=int(MinTimeCY/DT), dT=DT)
    acy_z500_objects, _ = clean_up_objects(objects_id_z500_high,min_tsteps=int(MinTimeACY/DT), dT=DT)


    cy_z500_objects = BreakupObjects(cy_z500_objects, min_tsteps=int(MinTimeCY / DT), dT=DT)
    acy_z500_objects = BreakupObjects(acy_z500_objects, min_tsteps=int(MinTimeACY / DT), dT=DT)

    end_time = time.time()
    logging.debug(f"{Fore.GREEN}======> 'z500 cyclone/anticyclone tracking: {(end_time-start_time):.2f} seconds \n")
    start_time = time.time()


    ######################################################################
    #####################################################################
    
    if nc_file is not None:
        logging.debug (f'{Style.BRIGHT} Save objects into a netCDF')

        fino=xr.Dataset({'cy_z500_objects':(['time','y','x'],cy_z500_objects),
                        'acy_z500_objects':(['time','y','x'],acy_z500_objects),
                        'z500':(['time','y','x'],z500_data),
                        'lat':(['y','x'],Lat),
                        'lon':(['y','x'],Lon)},
                            coords={'time':times.values})

        fino.to_netcdf(nc_file,mode='w',encoding={'z500':{'zlib': True,'complevel': 5},
                                                'cy_z500_objects':{'zlib': True,'complevel': 5},
                                                'acy_z500_objects':{'zlib': True,'complevel': 5}})


        end_time = time.time()
        logging.debug(f"{Style.BRIGHT} ======> 'Writing files: {(end_time-start_time):.2f} seconds \n")
        start_time = time.time()

    else:
        logging.debug(f"{Fore.YELLOW}No writing files required, output file name is empty")
    
    return cy_z500_objects, acy_z500_objects
#####################################################################
#####################################################################


def COL_tracking(cy_z500_objects,
                z500_data,
                u200_data,
                u850_data,
                v850_data,
                t850_data,
                pr_data,
                pr_data_max,
                times = None,
                Lon = None,
                Lat = None,
                nc_file = None):
    """ Function to determine if a cyclone is a cut-off low
    """

    start_time = time.time()

    #Reading tracking parameters
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

    #Calculating grid distances and areas
    _,_,grid_cell_area,grid_spacing = calc_grid_distance_area(Lat,Lon)
    grid_cell_area[grid_cell_area < 0] = 0

    #Check if cyclone is a cut-off low
    cy_z500_objects = split_objects(cy_z500_objects, distance_threshold = int(MaxDistCYFeatures/grid_spacing))

    object_indices_low = ndimage.find_objects(cy_z500_objects.astype(int))
        
    col_objects = np.zeros(cy_z500_objects.shape,dtype=int)

    y_size = Lat.shape[0]
    x_size = Lon.shape[1]
    
    front_objects = Front_tracking(u850_data,
                                   v850_data,
                                   t850_data,
                                   times,
                                   Lon,
                                   Lat)

    for iobj in range(len(object_indices_low)):
            
        if object_indices_low[iobj] is None:
            continue
        
        obj_act = cy_z500_objects[object_indices_low[iobj]] == iobj + 1
        if obj_act.shape[0] < col_min_dur/DT:
            #print('object too short')
            continue


        

        time_start = object_indices_low[iobj][0].start
        time_stop = object_indices_low[iobj][0].stop
        lat_start  = object_indices_low[iobj][1].start - int(col_buffer/grid_spacing)
        lat_stop   = object_indices_low[iobj][1].stop + int(col_buffer/grid_spacing)
        lon_start  = object_indices_low[iobj][2].start - int(col_buffer/grid_spacing)
        lon_stop   = object_indices_low[iobj][2].stop + int(col_buffer/grid_spacing)

       


        if lat_start < 0:
            lat_start = 0
        if lon_start < 0:
            lon_start = 0
        if lat_stop > z500_data.shape[1]:
            lat_stop = z500_data.shape[1]
        if lon_stop > z500_data.shape[2]:
            lon_stop = z500_data.shape[2]
            

        z500_slice = z500_data[time_start:time_stop,lat_start:lat_stop,lon_start:lon_stop]
        u200_slice = u200_data[time_start:time_stop,lat_start:lat_stop,lon_start:lon_stop]
        object_slice = np.copy(cy_z500_objects[time_start:time_stop,lat_start:lat_stop,lon_start:lon_stop])==iobj+1
        front_slice = front_objects[time_start:time_stop,lat_start:lat_stop,lon_start:lon_stop]
        lat_slice = Lat[lat_start:lat_stop,lon_start:lon_stop]
        lon_slice = Lon[lat_start:lat_stop,lon_start:lon_stop]
        
        # find location of z500 minimum
        z500_slice_obj = np.copy(z500_slice)
        z500_slice_obj[object_slice == 0] = np.nan
        

        logging.debug(f"{Fore.GREEN} Cyclone {iobj+1} starts at {times[time_start].strftime('%Y-%m-%d %HUTC')}")
        for tt in range(z500_slice_obj.shape[0]):
            
            if np.isnan(z500_slice_obj[tt]).all():
                #no object to process
                logging.debug(f"{Fore.YELLOW} Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is not COL because there is no object")
                object_slice[tt,:,:] = 0
                continue
            min_loc = np.nanargmin(z500_slice_obj[tt,:,:])      
            min_la = np.unravel_index(min_loc, z500_slice_obj[tt,:,:].shape)[0]
            min_lo = np.unravel_index(min_loc, z500_slice_obj[tt,:,:].shape)[1]

            la0 = min_la - int(col_buffer/grid_spacing)
            lo0 = min_lo - int(col_buffer/grid_spacing)
            la1 = min_la + int(col_buffer/grid_spacing) + 1
            lo1 = min_lo + int(col_buffer/grid_spacing) + 1


            if la0 < 0:
                la0 = 0
            if lo0 < 0:
                lo0 = 0
            
            if la1 > z500_slice_obj.shape[1]:   
                la1 = z500_slice_obj.shape[1]
            
            if lo1 > z500_slice_obj.shape[2]:
                lo1 = z500_slice_obj.shape[2]
                
            
            
            lat_reg = lat_slice[la0:la1,lo0:lo1]
            lon_reg = lon_slice[la0:la1,lo0:lo1]

            z500_reg = z500_slice[tt,la0:la1,lo0:lo1]
            u200_reg = u200_slice[tt,la0:la1,lo0:lo1]
            z500_reg_obj= z500_slice_obj[tt,la0:la1,lo0:lo1]
            object_reg = object_slice[tt,la0:la1,lo0:lo1]
            front_reg = front_slice[tt,la0:la1,lo0:lo1]
            min_z500_obj = z500_slice[tt,min_la,min_lo]

            #Check if radius around center has higher Z
            min_loc_tt = np.nanargmin(z500_reg_obj)
            min_la_tt = np.unravel_index(min_loc_tt, z500_reg_obj.shape)[0]
            min_lo_tt = np.unravel_index(min_loc_tt, z500_reg_obj.shape)[1]
            
            # COL should only occure between 20 and 70 degrees
            # https://journals.ametsoc.org/view/journals/clim/33/6/jcli-d-19-0497.1.xml
            if (abs(lat_reg[min_la_tt,min_lo_tt]) < 20) | (abs(lat_reg[min_la_tt,min_lo_tt]) > 70):
                logging.debug(f"{Fore.YELLOW}Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is not COL because of latitude (not in 20-70)")
                object_slice[tt,:,:] = 0
                continue 

            # remove cyclones that are close to the poles
            if np.max(np.abs(lat_reg)) > 88:
                logging.debug(f"{Fore.YELLOW}Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is too close to the poles")
                object_slice[tt,:,:] = 0
                continue

            if ((np.max(lat_reg[object_reg[:,:] == 1]) > col_max_lat) | (np.min(lat_reg[object_reg[:,:] == 1]) < col_min_lat) |
               (np.max(lon_reg[object_reg[:,:] == 1]) > col_max_lon) | (np.min(lon_reg[object_reg[:,:] == 1]) < col_min_lon)):
                logging.debug(f"{Fore.YELLOW}Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is too far north or south (or next to the border)")
                object_slice[tt,:,:] = 0
                continue

            if (~np.isnan(z500_slice_obj[tt])).sum == 0:
                #no object to process
                object_slice[tt,:,:] = 0
                continue


            #CRITERIA 1) at least col_percent_isolation*100 % of grid cells in ring have have 100 m higher Z than center
            
            rdist = haversine(lat_reg[min_la_tt,min_lo_tt],lon_reg[min_la_tt,min_lo_tt],lat_reg,lon_reg)
    
            ring = (rdist >= (col_ring_isolation - (grid_spacing)*2))  & (rdist <= (col_ring_isolation + (grid_spacing)*2))
            if np.sum((z500_reg[ring] - min_z500_obj)/const.g > col_thres_isolation) < np.sum(ring)*col_percent_isolation:
                    logging.debug(f"{Fore.YELLOW}Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is not COL because is not detached enough")
                    object_slice[tt,:,:] = 0
                    continue
            
            # CRITERIA 2) check if 200 hPa wind speed is eastward in the poleward direction of the cyclone
            if lat_reg[min_la_tt,min_lo_tt]>0:
                east_flow = u200_reg [0:min_la_tt,min_lo_tt]
            else:
                east_flow = u200_reg [min_la_tt:-1,min_lo_tt]

    
            if (east_flow.shape[0]!=0):
                if (np.min(east_flow) > 0):
                    logging.debug(f"{Fore.YELLOW}Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is not COL because of eastward flow")
                    object_slice[tt,:,:] = 0
                    continue
            elif (east_flow.shape[0]==0):
                logging.debug(f"{Fore.YELLOW}Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is not COL because of eastward flow, too close to upper boundary")
                object_slice[tt,:,:] = 0
                continue


            # CRITERIA 3) check if there is a front in the region
           
            # front_test = np.sum(np.abs(front_reg[:, min_lo_tt:]) > 1)
            # if front_test < 1:
            #     logging.debug(f'{Fore.YELLOW}yclone {iobj+1} at {tt} is not COL because of no front to the east')
            #     object_slice[tt,:,:] = 0
            #     continue
            if (min_z500_obj/const.g) > col_z500_threshold_min:
                logging.debug(f"{Fore.YELLOW}Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is not COL because of z500 threshold (not deep enough)")
                object_slice[tt,:,:] = 0
                continue


            # CRITERIA 4) Check if system is within region
            obj_mass_center = ndimage.measurements.center_of_mass(object_slice[tt,:,:])
            obj_track_lat=lat_slice[int(round(obj_mass_center[0])),int(round(obj_mass_center[1]))]    
            obj_track_lon=lon_slice[int(round(obj_mass_center[0])),int(round(obj_mass_center[1]))]   
            # if iobj == 525: import pdb; pdb.set_trace()  # fmt: skip
            if (obj_track_lat < col_region[2] or 
                obj_track_lat > col_region[3] or 
                obj_track_lon < col_region [0] or 
                obj_track_lon > col_region[1]):
                logging.debug(f"{Fore.YELLOW}Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is outside {col_region}")

                object_slice[tt,:,:] = 0
                continue

            # # CRITERIA 5) Check if system is not too close to borders (not closed system)

            if lat_start == 0 or lat_stop == y_size or lon_start == 0 or lon_stop == x_size:
                
                if lat_start == 0 and np.any(object_slice[tt,0,:]==1):
                    logging.debug(f"{Fore.YELLOW}Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is too close to the border")
                    object_slice[tt,:,:] = 0
                    continue
                if lat_stop == y_size and np.any(object_slice[tt,-1,:]==1):
                    logging.debug(f"{Fore.YELLOW}Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is too close to the border")
                    object_slice[tt,:,:] = 0
                    continue
                if lon_start == 0 and np.any(object_slice[tt,:,0]==1):
                    logging.debug(f"{Fore.YELLOW}Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is too close to the border")
                    object_slice[tt,:,:] = 0
                    continue
                if lon_stop == x_size and np.any(object_slice[tt,:,-1]==1):
                    logging.debug(f"{Fore.YELLOW}Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is not COL because it is too close to the border")
                    object_slice[tt,:,:] = 0
                    continue

        # CRITERIA 6) Remove objects that are too short after all checks
        obj_life = object_slice.sum(axis=(1,2))>0
        obj_life_true = np.where(obj_life)[0]

        if obj_life_true.size == 0:
            continue
        else:
            extended_obj_life = np.arange(obj_life_true[0], obj_life_true[-1] + 1)

        if extended_obj_life.size < col_min_dur/DT:
            logging.debug(f"{Fore.YELLOW}Cyclone {iobj+1} is not COL because it is too short after all other criteria applied")
            continue
        else:
            logging.debug(f"{Fore.GREEN}Cyclone {iobj+1} at {times[time_start+tt].strftime('%Y-%m-%d %HUTC')} is a COL")
            
        object_slice = object_slice.astype(int)
        object_slice[object_slice > 0] = iobj + 1
        object_slice= object_slice + col_objects[time_start:time_stop,lat_start:lat_stop,lon_start:lon_stop]
        col_objects[time_start:time_stop,lat_start:lat_stop,lon_start:lon_stop] = object_slice
        
    #Number of col_objects identified
    col_objects_ids = np.unique(col_objects)
    col_objects_ids = col_objects_ids[col_objects_ids > 0]
    logging.debug(f"{Fore.GREEN} {col_objects_ids.size} Cut-off lows found")



    ## Searching for Cut-Off lows within area:
    #Calcualate the center of mass of the cyclone
    
    # new_labels = relabel_to_consecutive(col_objects)
    
    # new_labels_ind = ndimage.find_objects(new_labels)

    # col_objects_new = np.zeros(new_labels.shape,dtype=int)

    # new_labels = np.copy(col_objects)
    # new_labels_ind = ndimage.find_objects(new_labels)
    # col_objects_new = np.zeros(new_labels.shape,dtype=int)

    # for nobj in range(len(new_labels_ind)):
    #     print(nobj)

        
    #     object_slice = np.copy(new_labels[new_labels_ind[nobj]])==nobj+1

    #     if object_slice.shape[0]< col_min_dur/DT:
    #         continue

    #     lat_slice = np.copy(Lat[new_labels_ind[nobj][1:]])
    #     lon_slice = np.copy(Lon[new_labels_ind[nobj][1:]])
    #     obj_mass_center = np.array([ndimage.measurements.center_of_mass(object_slice[tstep,:,:]) for tstep in range(object_slice.shape[0])])
    #     if np.all(np.isnan(obj_mass_center)):
    #         import pdb; pdb.set_trace()  # fmt: skip
    #         continue

    #     obj_mass_center = obj_mass_center[~np.isnan(obj_mass_center).any(axis=1)]
    #     obj_track = np.full([len(obj_mass_center), 2], np.nan)
    #     try:
    #         obj_track[:,0]=np.array([lat_slice[int(round(obj_loc[0])),int(round(obj_loc[1]))]    for tstep, obj_loc in enumerate(obj_mass_center)])
    #         obj_track[:,1]=np.array([lon_slice[int(round(obj_loc[0])),int(round(obj_loc[1]))]    for tstep, obj_loc in enumerate(obj_mass_center)])
    #     except:
    #         import pdb; pdb.set_trace()

    #     track_in_region = np.asarray([(obj_track[tstep, 0] > col_region[2] and obj_track[tstep, 0] < col_region[3] and 
    #          obj_track[tstep, 1] > col_region[0] and obj_track[tstep, 1] < col_region[1]) 
    #         for tstep in range(obj_track.shape[0])])
        



    #     if track_in_region.sum() == 0:
    #         logging.debug(f'{Fore.YELLOW}Cyclone {nobj+1} outside region of interest {col_region}')
    #         continue
    #     else:
    #         logging.debug(f'{Fore.GREEN}Cyclone {nobj+1} inside region of interest {col_region}')
    #         print(obj_track)
    #         import pdb; pdb.set_trace()  # fmt: skip
    #         col_objects_new[new_labels==nobj+1] = nobj+1

    
    #new_labels2 = relabel_to_consecutive(col_objects_new)


    if nc_file is not None:
        logging.debug (f'{Style.BRIGHT} Save objects into a netCDF')
        
        
        

        
        fino=xr.Dataset({'cy_z500_objects':(['time','latitude','longitude'],cy_z500_objects),
                            'col_objects':(['time','latitude','longitude'],col_objects),
                            'front_objects':(['time','latitude','longitude'],front_objects),
                            'z500':(['time','latitude','longitude'],z500_data),
                            'u200':(['time','latitude','longitude'],u200_data),
                            't850':(['time','latitude','longitude'],t850_data),
                            'u850':(['time','latitude','longitude'],u850_data),
                            'v850':(['time','latitude','longitude'],v850_data),
                            'pr':(['time','latitude','longitude'],pr_data),
                            'pr_max':(['time','latitude','longitude'],pr_data_max)
                            },
                            coords={'time':times.values,'latitude':Lat[:,0].squeeze(),'longitude':Lon[0,:].squeeze()})
        
        # Adding units to 'pr' and 'pr_max'
        fino['pr'].attrs['units'] = 'mm'  
        fino['pr_max'].attrs['units'] = 'mm/hr'  

        # Optionally, you can add a description or other metadata
        fino['pr'].attrs['description'] = 'Accumulated Precipitation'
        fino['pr_max'].attrs['description'] = 'Maximum precipitation rate'

        reference_date = np.datetime64("1940-01-01T00:00:00")
        fino['time'] = (fino['time'] - reference_date) / np.timedelta64(1, 'h')
        fino['time'].attrs['units'] = f"hours since {reference_date}"
        fino['time'].attrs['calendar'] = 'standard'


        fino.to_netcdf(nc_file,mode='w',
                        format="NETCDF4",
                        encoding={'z500':{'zlib': True,'complevel': 5},
                                            'u200':{'zlib': True,'complevel': 5},
                                            'cy_z500_objects':{'zlib': True,'complevel': 5},
                                            'col_objects':{'zlib': True,'complevel': 5}})


        end_time = time.time()
        logging.debug(f"{Style.BRIGHT} ======> 'Writing files: {(end_time-start_time):.2f} seconds \n")
        start_time = time.time()

    else:
        logging.debug(f"{Fore.YELLOW}No writing files required, output file name is empty")


    return col_objects