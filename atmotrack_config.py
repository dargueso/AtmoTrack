#####################################################################
#####################################################################

# General options

#path_in = "/vg6/dargueso-NO-BKUP/postprocessed/unified/EPICC/EPICC_2km_ERA5_CMIP6anom_HVC_GWD/"
DT = 6 # time step of data in hours


#####################################################################
#####################################################################
# MCS tracking options

MCSvariables = ["PR", "Tb"]

# MINIMUM REQUIREMENTS FOR FEATURE DETECTION
# precipitation tracking options
smooth_sigma_pr = 0 # Gaussion std for precipitation smoothing
thres_pr = 5  # precipitation threshold [mm/h]
min_time_pr= 3  # minum lifetime of PR feature in hours
min_area_pr = 500  # minimum area of precipitation feature in km2

# Brightness temperature (Tb) tracking setup
smooth_sigma_bt = 0  # Gaussion std for Tb smoothing
thres_bt = 241  # minimum Tb of cloud shield
min_time_bt = 5  # minium lifetime of cloud shield in hours
min_area_bt = 1000  # minimum area of cloud shield in km2

# MCs detection
MCS_min_area = min_area_pr  # minimum area of MCS precipitation object in km2
MCS_thres_pr = 5  # minimum max precipitation in mm/h
MCS_thres_peak_pr = 15  # Minimum lifetime peak of MCS precipitation
MCS_thres_bt = 225  # minimum brightness temperature
MCS_min_area_bt = min_area_bt  # min cloud area size in km2
MCS_min_time = 5  # minimum lifetime of MCS


#####################################################################
#####################################################################

#CY_ACY_z500 config

smooth_sigma_z500 = 0  # Gaussion std for z500 smoothing
z500_smooth_low_anom = -80 # lower threshold for smooth z500 anomaly
z500_smooth_high_anom = 70 # upper threshold for smooth z500 anomaly


MinTimeCY = 12             # minimum livetime of cyclones [hours]
MinTimeACY = 12             # minimum livetime of anticyclone [hours]

MaxDistCYFeatures = 500000 # maximum distance features in cyclones and anticyclones in m to split them

#####################################################################
#####################################################################


## COL config
# Cut-Off Low tracking options

col_buffer = 600000 # area around the cyclone in m
col_min_dur = 24 # minimum livetime of COL [hours]
col_z500_threshold_min = 9999 # minimum z500 value to consider a COL [m] Set to 9999 to disable
col_region = [-15, 10, 30, 45] # region to track COLs, the centre of mass must be inside this region at some point [lon_min, lon_max, lat_min, lat_max]
col_percent_isolation = 0.90 # percentage of points in the ring that must be above a threshold with respect to the minimum value to consider it isolated
col_ring_isolation = 600000 # distance in m from the centre of the COL to the ring to check isolation
col_thres_isolation = 60 # minimum difference in z500 (m) between minimum and points in the ring to define isolation
col_min_lat = 25 # all grid points of the system must be north of this to be considered a COL
col_max_lat = 60 # all grid points of the system must be south of this to be considered a COL
col_min_lon = -29 # all grid points of the system must be east of this to be considered a COL
col_max_lon = 19 # all grid points of the system must be west of this to be considered a COL
#####################################################################
#####################################################################

# Fronts tracking options

front_treshold=0.5
MinAreaFR=50000