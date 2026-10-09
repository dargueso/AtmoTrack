#!/usr/bin/env python
"""Shared helper functions used by all tracking submodules."""

import logging
import pickle

import numpy as np
import scipy.ndimage as filters
from scipy import ndimage
from scipy.ndimage import distance_transform_edt

from atmotrack.constants import const

logger = logging.getLogger("atmotrack")


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
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    c = 2 * np.arcsin(np.sqrt(a))
    # Radius of earth in kilometers is 6371
    dist_m = c * const.earth_radius
    return dist_m


def calc_grid_distance_area(lat, lon):
    """Function to calculate grid parameters
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

    dx[:, 1:] = haversine(lat[:, 1:], lon[:, 1:], lat[:, :-1], lon[:, :-1])
    dy[1:, :] = haversine(lat[1:, :], lon[1:, :], lat[:-1, :], lon[:-1, :])

    dx[:, 0] = dx[:, 1]
    dy[0, :] = dy[1, :]

    area = dx * dy
    grid_distance = np.mean(np.append(dy[:, :, None], dx[:, :, None], axis=2))

    return dx, dy, area, grid_distance


def smooth_uniform(data, time_size, spatial_size):
    """Uniform filter helper: size=(time_size, spatial_size, spatial_size)."""
    return filters.uniform_filter(data, size=(time_size, spatial_size, spatial_size))


def calculate_area_objects(objects_id_pr, object_indices, grid_cell_area):
    """Calculates the area of each object during their lifetime
    one area value for each object and each timestep it exist
    """
    num_objects = len(object_indices)
    area_objects = np.array(
        [
            [
                np.sum(
                    grid_cell_area[object_indices[obj][1:]][
                        objects_id_pr[object_indices[obj]][tstep, :, :] == obj + 1
                    ]
                )
                for tstep in range(objects_id_pr[object_indices[obj]].shape[0])
            ]
            for obj in range(num_objects)
        ],
        dtype=object,
    )

    return area_objects


def remove_small_short_objects(objects_id, area_objects, min_area, min_time, DT):
    """Checks if the object is large enough during enough time steps
    and removes objects that do not meet this condition
    area_object: array of lists with areas of each objects during their lifetime [objects[tsteps]]
    min_area: minimum area of the object (km2)
    min_time: minimum time with the object large enough (hours)
    """

    # create final object array
    sel_objects = np.zeros(objects_id.shape, dtype=int)

    new_obj_id = 1
    for obj, _ in enumerate(area_objects):
        AreaTest = np.max(
            np.convolve(
                np.array(area_objects[obj]) >= min_area * 1000**2,
                np.ones(int(min_time / DT)),
                mode="valid",
            )
        )

        if (AreaTest == int(min_time / DT)) & (len(area_objects[obj]) >= int(min_time / DT)):
            sel_objects[objects_id == (obj + 1)] = new_obj_id
            new_obj_id += 1

    return sel_objects


def relabel_to_consecutive(labels):
    unique_labels = np.unique(labels)  # Get unique labels
    new_labels = np.zeros_like(labels)  # Initialize array with the same shape as input
    mapping = {old_label: new_label for new_label, old_label in enumerate(unique_labels)}

    for old_label, new_label in mapping.items():
        new_labels[labels == old_label] = new_label

    return new_labels


def calc_object_characteristics(
    var_objects,  # feature object file
    var_data,  # original file used for feature detection
    filename_out,  # output file name and locaiton
    times,  # timesteps of the data
    Lat,  # 2D latidudes
    Lon,  # 2D Longitudes
    grid_spacing,  # average grid spacing
    grid_cell_area,
    min_tsteps=1,  # minimum lifetime in data timesteps
):
    # ========

    num_objects = int(var_objects.max())
    object_indices = ndimage.find_objects(var_objects)

    if num_objects < 1:
        return {}

    if num_objects >= 1:
        objects_charac = {}
        logger.debug("            Loop over " + str(num_objects) + " objects")
        for iobj in range(num_objects):
            object_slice = np.copy(var_objects[object_indices[iobj]])
            data_slice = np.copy(var_data[object_indices[iobj]])
            time_idx_slice = object_indices[iobj][0]
            lat_idx_slice = object_indices[iobj][1]
            lon_idx_slice = object_indices[iobj][2]

            # if len(object_slice) >= min_tsteps:
            if np.sum(np.any(object_slice == iobj + 1, axis=(1, 2))) >= min_tsteps:
                data_slice[object_slice != (iobj + 1)] = np.nan
                grid_cell_area_slice = np.tile(
                    grid_cell_area[lat_idx_slice, lon_idx_slice], (len(data_slice), 1, 1)
                )
                grid_cell_area_slice[object_slice != (iobj + 1)] = np.nan
                lat_slice = Lat[lat_idx_slice, lon_idx_slice]
                lon_slice = Lon[lat_idx_slice, lon_idx_slice]

                # calculate statistics
                obj_times = times[time_idx_slice]
                obj_size = np.nansum(grid_cell_area_slice, axis=(1, 2))
                obj_min = np.nanmin(data_slice, axis=(1, 2))
                obj_max = np.nanmax(data_slice, axis=(1, 2))
                obj_mean = np.nanmean(data_slice, axis=(1, 2))
                obj_tot = np.nansum(data_slice, axis=(1, 2))

                # Track lat/lon
                obj_mass_center = np.array(
                    [
                        ndimage.center_of_mass(object_slice[tt, :, :] == (iobj + 1))
                        for tt in range(object_slice.shape[0])
                    ]
                )

                if np.any(np.isnan(obj_mass_center)):
                    raise ValueError("mass center array contains NaNs")

                obj_track = np.full([len(obj_mass_center), 2], np.nan)

                idx = np.round(obj_mass_center).astype(int)
                idx[:, 0] = np.clip(idx[:, 0], 0, lat_slice.shape[0] - 1)
                idx[:, 1] = np.clip(idx[:, 1], 0, lat_slice.shape[1] - 1)
                obj_track[:, 0] = lat_slice[idx[:, 0], idx[:, 1]]
                obj_track[:, 1] = lon_slice[idx[:, 0], idx[:, 1]]

                if np.any(np.isnan(obj_track)):
                    raise ValueError("track array contains NaNs")

                obj_speed = (np.sum(np.diff(obj_mass_center, axis=0) ** 2, axis=1) ** 0.5) * (
                    grid_spacing / 1000.0
                )

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
                except Exception as exc:
                    raise ValueError("Error assigning properties to final dictionary") from exc

        if filename_out is not None:
            with open(filename_out, "wb") as handle:
                pickle.dump(objects_charac, handle)

        return objects_charac


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
                for ii, _ in enumerate(OBJ_Left)
            ]
        )
        NotSame = OBJ_Left != OBJ_Right
        OBJ_joint = OBJ_joint[NotSame]
        OBJ_unique = np.unique(OBJ_joint)
        # set the eastern object to the number of the western object in all timesteps
        for obj, _ in enumerate(OBJ_unique):
            ObE = int(OBJ_unique[obj].split("_")[1])
            ObW = int(OBJ_unique[obj].split("_")[0])
            object_indices[object_indices == ObE] = ObW
    return object_indices


def ConnectLon_on_timestep(object_indices):
    """Connect objects split across the date line, one timestep at a time.

    Unlike ``ConnectLon`` (which relabels globally), this function only merges
    the left/right edge objects within each individual timestep.  This is
    required after ``BreakupObjects`` which can assign different labels to the
    same physical object at different times.
    """
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
                for ii, _ in enumerate(OBJ_Left)
            ]
        )
        NotSame = OBJ_Left != OBJ_Right
        OBJ_joint = OBJ_joint[NotSame]
        OBJ_unique = np.unique(OBJ_joint)
        for obj, _ in enumerate(OBJ_unique):
            ObE = int(OBJ_unique[obj].split("_")[1])
            ObW = int(OBJ_unique[obj].split("_")[0])
            object_indices[tt, object_indices[tt, :] == ObE] = ObW
    return object_indices


### Break up long living cyclones by extracting the biggest cyclone at each time
def BreakupObjects(
    DATA,  # 3D matrix [time,lat,lon] containing the objects
    min_tsteps,  # minimum lifetime in data timesteps
    dT,
):  # time step in hours

    object_indices = ndimage.find_objects(DATA)
    MaxOb = np.max(DATA)
    int(24 / dT)  # min lifetime of object to be split
    AVmax = 1.5

    obj_structure_2D = np.zeros((3, 3, 3))
    obj_structure_2D[1, :, :] = 1
    rgiObjects2D, nr_objects2D = ndimage.label(DATA, structure=obj_structure_2D)

    rgiObjNrs = np.unique(DATA)[1:]
    TT = np.array(
        [object_indices[obj][0].stop - object_indices[obj][0].start for obj in range(MaxOb)]
    )
    # Sel_Obj = rgiObjNrs[TT > MinLif]

    # Average 2D objects in 3D objects?
    Av_2Dob = np.zeros(len(rgiObjNrs))
    Av_2Dob[:] = np.nan
    ii = 1
    for obj, _ in enumerate(rgiObjNrs):
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
                [len(np.unique(rgiObjects2D_ACT[tt, :, :])) - 1 for tt in range(DATA_ACT.shape[0])]
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
                        np.unique(rgiObjects2D_ACT[tt, rgiObjects2D_ACT[tt - 1, :] == ob1])[1:]
                    )
                    if len(tt1_obj) == 0:
                        # this object ends here
                        rgiObActCP.remove(ob1)
                        continue
                    elif len(tt1_obj) == 1:
                        rgiObjects2D_ACT[tt, rgiObjects2D_ACT[tt, :] == tt1_obj[0]] = ob1
                    else:
                        VOL = [
                            np.sum(rgiObjects2D_ACT[tt, :] == tt1_obj[jj])
                            for jj, _ in enumerate(tt1_obj)
                        ]
                        rgiObjects2D_ACT[tt, rgiObjects2D_ACT[tt, :] == tt1_obj[np.argmax(VOL)]] = (
                            ob1
                        )
                        tt1_obj.remove(tt1_obj[np.argmax(VOL)])
                        rgiObActCP = rgiObActCP + list(tt1_obj)

                # make sure that mergers are assigned the largest object
                for ob2 in rgiObActCP:
                    ttm1_obj = list(
                        np.unique(rgiObjects2D_ACT[tt - 1, rgiObjects2D_ACT[tt, :] == ob2])[1:]
                    )
                    if len(ttm1_obj) > 1:
                        VOL = [
                            np.sum(rgiObjects2D_ACT[tt - 1, :] == ttm1_obj[jj])
                            for jj, _ in enumerate(ttm1_obj)
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
            for obj, _ in enumerate(Unique)
        ]
    )
    TT = np.array(
        [
            object_indices[Unique[obj] - 1][0].stop - object_indices[Unique[obj] - 1][0].start
            for obj, _ in enumerate(Unique)
        ]
    )

    # create final object array
    CY_objectsTMP = np.copy(DATA)
    CY_objectsTMP[:] = 0
    ii = 1
    for obj, _ in enumerate(rgiVolObj):
        if TT[obj] >= min_tsteps / dT:
            CY_objectsTMP[DATA == Unique[obj]] = ii
            ii = ii + 1

    # lable the objects from 1 to N
    DATA_fin = np.copy(CY_objectsTMP)
    DATA_fin[:] = 0
    Unique = np.unique(CY_objectsTMP)[1:]
    ii = 1
    for obj, _ in enumerate(Unique):
        DATA_fin[CY_objectsTMP == Unique[obj]] = ii
        ii = ii + 1

    return DATA_fin


def clean_up_objects(DATA, dT, min_tsteps=0, obj_splitmerge=None):
    """Function to remove objects that are too short lived
    and to numerrate the object from 1...N
    """

    object_indices = ndimage.find_objects(DATA)
    np.max(DATA)
    int(24 / dT)  # min lifetime of object to be split

    id_translate = np.zeros((len(object_indices), 2))
    objectsTMP = np.copy(DATA)
    objectsTMP[:] = 0
    ii = 1
    for obj in range(len(object_indices)):
        if object_indices[obj] is not None:
            if object_indices[obj][0].stop - object_indices[obj][0].start >= min_tsteps / dT:
                Obj_tmp = np.copy(objectsTMP[object_indices[obj]])
                Obj_tmp[DATA[object_indices[obj]] == obj + 1] = ii
                objectsTMP[object_indices[obj]] = Obj_tmp
                id_translate[obj, 0] = obj + 1
                id_translate[obj, 1] = ii
                ii = ii + 1
            else:
                id_translate[obj, 0] = obj + 1
                id_translate[obj, 1] = -1
        else:
            id_translate[obj, 0] = obj + 1
            id_translate[obj, 1] = -1

    # adjust the directory strucutre accordingly
    obj_splitmerge_clean = {}

    if obj_splitmerge is not None:
        id_translate = id_translate.astype(int)
        keys = np.copy(list(obj_splitmerge.keys()))
        for jj in range(len(keys)):
            obj_loc = np.where(int(list(keys)[jj]) == id_translate[:, 0])[0][0]
            if id_translate[obj_loc, 1] == -1:
                del obj_splitmerge[list(keys)[jj]]

        # loop over objects and relable their indices if nescessary
        obj_splitmerge_clean = {}
        keys = np.copy(list(obj_splitmerge.keys()))
        core_translate = np.isin(id_translate[:, 0], keys.astype(int))
        id_translate = id_translate[core_translate, :]
        for jj in range(len(keys)):
            obj_loc = np.where(int(list(keys)[jj]) == id_translate[:, 0])[0][0]
            mergsplit = np.array(obj_splitmerge[keys[jj]])
            for kk in range(id_translate.shape[0]):
                mergsplit[np.isin(mergsplit, id_translate[kk, 0])] = id_translate[kk, 1]
            obj_splitmerge_clean[str(int(id_translate[obj_loc, 1]))] = mergsplit

    return objectsTMP, obj_splitmerge_clean


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
                    for j, region_j in enumerate(regions[i + 1 :], start=i + 1):
                        # Calculate the maximum distance in the distance map for each region
                        max_dist_i = distance_map[tuple(region_i.T)].max()
                        max_dist_j = distance_map[tuple(region_j.T)].max()

                        # Check if the maximum distance between these regions exceeds the threshold
                        distance = np.linalg.norm(region_i.mean(axis=0) - region_j.mean(axis=0))
                        if distance > distance_threshold or (
                            max_dist_i > distance_threshold and max_dist_j > distance_threshold
                        ):
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
                    default=(None, None),
                )
                # Relabel if the closest previous label is within the distance threshold
                if (
                    closest_prev_label is not None
                    and np.linalg.norm(closest_prev_pos - current_pos) < distance_threshold
                ):
                    relabel_updates[current_label] = closest_prev_label

        # Apply relabel updates after iteration to avoid modifying during the loop
        for current_label, new_label in relabel_updates.items():
            new_objects[t][new_objects[t] == current_label] = new_label
            current_labels_positions[new_label] = current_labels_positions.pop(
                current_label
            )  # Update position with new label

        # Update last positions for the next timestep
        last_labels_positions = current_labels_positions

    return new_objects
