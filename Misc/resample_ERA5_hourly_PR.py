#!/usr/bin/env python
'''
@File    :  resample_ERA5_hourly_PR.py
@Time    :  2024/11/24 12:10:36
@Author  :  Daniel Argüeso
@Version :  1.0
@Contact :  d.argueso@uib.es
@License :  (C)Copyright 2022, Daniel Argüeso
@Project :  None
@Desc    :  None
'''

import xarray as xr
import numpy as np

from glob import glob

filesin = sorted(glob('./era5_daily_PR_1940.nc'))

nfiles = len(filesin)
nyears = nfiles

for nf, filein in enumerate(filesin):

    print(f'Processing file {filein} ({nf+1}/{nfiles})')
    fin_pr = xr.open_dataset(filein)

    pr_data = fin_pr.tp.resample(valid_time="6h").sum()
    pr_data_max = fin_pr.tp.resample(valid_time="6h").max()

    import pdb; pdb.set_trace()  # fmt: skip