#####################################################################
#####################################################################

# General options

path_in = "/vg6/dargueso-NO-BKUP/postprocessed/unified/EPICC/EPICC_2km_ERA5_CMIP6anom_HVC_GWD/"
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

#####################################################################
#####################################################################


## COL config
# Cut-Off Low tracking options

col_buffer = 500000 # area around the cyclone in m