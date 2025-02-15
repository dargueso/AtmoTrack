#!/usr/bin/env python
"""
#####################################################################
# Author: Daniel Argueso <daniel>
# Date:   2022-03-28T11:27:09+02:00
# Email:  d.argueso@uib.es
# Last modified by:   daniel
# Last modified time: 2022-03-28T11:27:14+02:00
#
# @Project@ EPICC
# Version: 1.0 (Beta)
# Description: This program ingest RAIN and OLR postprocessed files to identify
# and track storms. It uses RAIN and WINDSPEED to calculate storm statistics
#
# Dependencies:
#
# Files:
#
# Based on Andreas Prein version 2022
# (https://colab.research.google.com/drive/1MrQFujQCFhesk0MCUSqB41Mx3AHEd1ua?usp=sharing)
#####################################################################
"""
from glob import glob
import time
import logging
import numpy as np

import xarray as xr
import pandas as pd

from joblib import Parallel, delayed

import atmotrack_config as cfg
from constants import const
from tracking_functions import CY_ACY_z500_tracking,COL_tracking

from colorama import Fore, Style, init
init(autoreset=True)

#logging.basicConfig(format='%(asctime)s | %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S',level=logging.INFO)

###########################################################
###########################################################
def start_logger_if_necessary():
    logger = logging.getLogger("mylogger")
    if len(logger.handlers) == 0:
        logger.setLevel(logging.DEBUG)
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
        fh = logging.FileHandler('out.log', mode='w')
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
        logger.addHandler(sh)
        logger.addHandler(fh)
    return logger


###########################################################
###########################################################


def main():
    """ Main program: loops over available files and parallelize storm tracking
    """
    #start_logger_if_necessary()
    filesin = sorted(
            glob(f"./era5_daily_500hPa_2024.nc")
    )

    Parallel(n_jobs=1)(delayed(cutofflow_tracking)(fin_name) for fin_name in filesin)

###########################################################
###########################################################


def cutofflow_tracking(z500_finname):
    """ Initialize the algorithm loading data from postprocessed WRF
    """
    logger = start_logger_if_necessary()
    logger.info(f"Analyzing {z500_finname}")
    
    #logging.info(f"Analyzing {pr_finname}")
    start_time = time.time()

    
    z500 = xr.open_dataset(f"{z500_finname}").squeeze()
    z200  = xr.open_dataset(f"{z500_finname.replace('500hPa','200hPa')}").squeeze()
    z850  = xr.open_dataset(f"{z500_finname.replace('500hPa','850hPa')}").squeeze()
    pr = xr.open_dataset(f"{z500_finname.replace('500hPa','PR')}").squeeze()

    z500_data = z500.z.values
    u200_data = z200.u.values
    t850_data = z850.t.values
    u850_data = z850.u.values
    v850_data = z850.v.values
    pr_data = pr.tp.resample(valid_time="6h").sum().values*1000.
    pr_data_max = pr.tp.resample(valid_time="6h").max().values*1000.

    if z500_data.shape != pr_data.shape:
        logging.debug(f"{Fore.YELLOW} WARNING: Data shapes do not match: {z500_data.shape} vs {pr_data.shape}")
        diff_times = z500_data.shape[0] - pr_data.shape[0]
        pr_data = np.pad(pr_data, ((diff_times, 0), (0, 0), (0, 0)), mode='constant', constant_values=0)
        pr_data_max = np.pad(pr_data_max, ((diff_times, 0), (0, 0), (0, 0)), mode='constant', constant_values=0)

    lat = z500.latitude.values
    lon = z500.longitude.values

    lon2d,lat2d = np.meshgrid(lon, lat)

    times = pd.date_range(z500.valid_time.isel(valid_time=0).values, end=z500.valid_time.isel(valid_time=-1).values, freq='6h')

    end_time = time.time()
    logging.debug(f"======> 'Loading data: {(end_time-start_time):.2f} seconds \n")

    ###########################################################
    ###########################################################



    #fileout_cy = z500_finname.replace("500hPa", "cy_z500")
    fileout_col = z500_finname.replace("500hPa", "col_z500")
    

    cy_z500_objects,_ = CY_ACY_z500_tracking(
        z500_data,
        times,
        lon2d,
        lat2d,
        nc_file          =   None,
    )
    
        

    _ = COL_tracking(cy_z500_objects,
                z500_data,
                u200_data,
                u850_data,
                v850_data,
                t850_data,
                pr_data,
                pr_data_max,
                times = times,
                Lon = lon2d,
                Lat = lat2d,
                nc_file = fileout_col)

    end_time = time.time()
    logging.info(f"======> DONE in {(end_time-start_time):.2f} seconds \n")

    #fout_name = f'{cfg.path_in}/{wrun}/Storm_properties_{sdate.year}-{sdate.month:02d}.pkl'
    #pickle.dump(grMCSs,open(fout_name,'wb'))
###############################################################################
##### __main__  scope
###############################################################################

if __name__ == "__main__":
    logging.basicConfig(format='%(asctime)s | %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S',level=logging.DEBUG)
    main()

###########################################################
###########################################################
