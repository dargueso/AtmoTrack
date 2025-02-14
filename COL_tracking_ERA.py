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
from tracking_functions import CY_ACY_z500_tracking,COLtracking



#logging.basicConfig(format='%(asctime)s | %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S',level=logging.INFO)

###########################################################
###########################################################
def start_logger_if_necessary():
    logger = logging.getLogger("mylogger")
    if len(logger.handlers) == 0:
        logger.setLevel(logging.INFO)
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
        glob(f"./era5_daily_500hPa_19????.nc")
    )



    Parallel(n_jobs=1)(delayed(cy_z500_tracking)(fin_name) for fin_name in filesin)

###########################################################
###########################################################


def cy_z500_tracking(z500_finname):
    """ Initialize the algorithm loading data from postprocessed WRF
    """
    logger = start_logger_if_necessary()
    logger.info(f"Analyzing {z500_finname}")
    #logging.info(f"Analyzing {pr_finname}")
    start_time = time.time()

    
    z500 = xr.open_dataset(f"{z500_finname}").squeeze()
    # WSPD  = xr.open_dataset(f"{pr_finname.replace('RAIN','WSPD10')}").isel(time=slice(216,240)).squeeze()

    z500_data = z500.z.values

    lat = z500.latitude.values
    lon = z500.longitude.values

    lon2d,lat2d = np.meshgrid(lon, lat)

    times = pd.date_range(z500.valid_time.isel(valid_time=0).values, end=z500.valid_time.isel(valid_time=-1).values, freq='6H')

    end_time = time.time()
    logging.debug(f"======> 'Loading data: {(end_time-start_time):.2f} seconds \n")

    ###########################################################
    ###########################################################



    fileout = z500_finname.replace("500hPa", "cy_z500")


    _,_ = CY_ACY_z500_tracking(
        z500_data,
        times,
        lon2d,
        lat2d,
        nc_file          =   fileout,
    )

    end_time = time.time()
    logging.info(f"======> DONE in {(end_time-start_time):.2f} seconds \n")

    #fout_name = f'{cfg.path_in}/{wrun}/Storm_properties_{sdate.year}-{sdate.month:02d}.pkl'
    #pickle.dump(grMCSs,open(fout_name,'wb'))
###############################################################################
##### __main__  scope
###############################################################################

if __name__ == "__main__":
    logging.basicConfig(format='%(asctime)s | %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S',level=logging.INFO)
    main()

###########################################################
###########################################################
