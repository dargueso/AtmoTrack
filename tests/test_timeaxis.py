"""Time axes: standard and cftime calendars, step detection, output encoding."""

import cftime
import numpy as np
import pandas as pd
import xarray as xr

from atmotrack import timeaxis
from tests.conftest import LAT2D, LON2D, z500_field


def test_step_hours_and_regularity():
    t = pd.date_range("2000-01-01", periods=10, freq="6h")
    assert timeaxis.step_hours(t) == 6.0
    assert timeaxis.is_regular(t)
    irregular = t.delete(3)
    assert timeaxis.step_hours(irregular) == 6.0
    assert not timeaxis.is_regular(irregular)


def test_on_step_mask():
    t = pd.date_range("2000-01-01", periods=24, freq="1h")
    mask = timeaxis.on_step_mask(t, 6)
    assert mask.sum() == 4
    assert list(t[mask].hour) == [0, 6, 12, 18]


def _noleap_times(n=28):
    return xr.CFTimeIndex(
        [cftime.DatetimeNoLeap(2001, 2, 27) + i * pd.Timedelta(hours=6) for i in range(n)]
    )


def test_noleap_index_roundtrip(tmp_path):
    times = _noleap_times()
    assert timeaxis.calendar_of(times) == "noleap"
    assert timeaxis.step_hours(times) == 6.0
    assert list(np.unique(timeaxis.years(times))) == [2001]
    # Feb 29 does not exist in noleap: 27 Feb + 2 days -> 1 Mar
    assert times[8].month == 3 and times[8].day == 1

    ds = xr.Dataset({"x": ("time", np.arange(len(times)))}, coords={"time": times})
    path = tmp_path / "noleap.nc"
    ds.to_netcdf(path, encoding={"time": timeaxis.encode_times(times)})
    with xr.open_dataset(path) as back:
        loaded = timeaxis.load_times(back, "time")
    assert isinstance(loaded, xr.CFTimeIndex)
    assert timeaxis.calendar_of(loaded) == "noleap"
    assert loaded[8] == times[8]


def test_tracker_writes_noleap_output(tmp_path):
    from atmotrack.tracking import CY_ACY_z500_tracking

    times = _noleap_times()
    z = z500_field(nt=len(times))
    out = tmp_path / "cy_z500_noleap.nc"
    cy, _ = CY_ACY_z500_tracking(z, times, LON2D, LAT2D, nc_file=str(out))
    assert cy.max() > 0
    with xr.open_dataset(out) as ds:
        loaded = timeaxis.load_times(ds, "time")
        assert timeaxis.calendar_of(loaded) == "noleap"
        assert ds["cy_z500_objects"].dims == ("time", "latitude", "longitude")
        assert "lat" in ds.coords and ds["lat"].dims == ("latitude", "longitude")
