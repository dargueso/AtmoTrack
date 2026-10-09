"""atmotrack-check-input and atmotrack-prepare on a synthetic non-ERA5 collection.

The source has: longitudes in 0..360, ascending latitude, geopotential height
in m, pressure in hPa, humidity in g/kg, temperature in °C, hourly
precipitation as a rate in kg m-2 s-1, a size-1 ``plev`` dimension, a noleap
calendar and a ``time`` coordinate.  After preparing, the trackers must read
the canonical files directly.
"""

import cftime
import numpy as np
import pandas as pd
import xarray as xr

from atmotrack import config as cfg
from atmotrack.cli import check_input as check_cli
from atmotrack.cli import prepare as prepare_cli
from atmotrack.inputcheck import PREPARE, READY, check_all, overall_status
from atmotrack.prepare import prepare_year

LAT = np.arange(10.0, 75.5, 0.5)  # ascending
LON = (
    np.arange(330.0, 360.0, 0.5).tolist() + np.arange(0.0, 20.5, 0.5).tolist()
)  # 0..360, crossing 0°
LON = np.array(LON)


def _times(step_h, n_days=7):
    n = int(n_days * 24 / step_h)
    return xr.CFTimeIndex(
        [cftime.DatetimeNoLeap(2001, 2, 25) + i * pd.Timedelta(hours=step_h) for i in range(n)]
    )


def _write(path, times, variables, attrs, plev=True):
    dims = ("time", "plev", "lat", "lon") if plev else ("time", "lat", "lon")
    data_vars = {}
    for name, values in variables.items():
        v = np.asarray(values, dtype=np.float32)
        if plev:
            v = v[:, None, :, :]
        data_vars[name] = (dims, v, attrs.get(name, {}))
    coords = {"time": times, "lat": LAT, "lon": LON}
    if plev:
        coords["plev"] = [50000.0]
    xr.Dataset(data_vars, coords=coords).to_netcdf(path)


def _source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    lon2d, lat2d = np.meshgrid(((LON + 180) % 360) - 180, LAT)
    t6 = _times(6)
    nt = len(t6)
    zg = np.empty((nt,) + lon2d.shape)
    for t in range(nt):
        lon_c = -12.0 + t * 0.4
        zg[t] = 5_400.0 - 250.0 * np.exp(-((lon2d - lon_c) ** 2 + (lat2d - 40.0) ** 2) / 20.0)
    rng = np.random.default_rng(1)
    _write(src / "model_z500_2001.nc", t6, {"zg": zg}, {"zg": {"units": "m"}})
    _write(
        src / "model_z200_2001.nc",
        t6,
        {"ua": rng.normal(0, 20, zg.shape), "va": rng.normal(0, 20, zg.shape)},
        {"ua": {"units": "m s-1"}, "va": {"units": "m s-1"}},
    )
    _write(
        src / "model_z850_2001.nc",
        t6,
        {
            "ua": rng.normal(0, 10, zg.shape),
            "va": rng.normal(0, 10, zg.shape),
            "ta": np.full(zg.shape, 5.0),
            "hus": np.full(zg.shape, 5.0),
        },
        {
            "ua": {"units": "m s-1"},
            "va": {"units": "m s-1"},
            "ta": {"units": "degC"},
            "hus": {"units": "g kg-1"},
        },
    )
    t3 = _times(3)
    psl = np.full((len(t3),) + lon2d.shape, 1013.25)
    _write(src / "model_psl_2001.nc", t3, {"psl": psl}, {"psl": {"units": "hPa"}}, plev=False)
    t1 = _times(1)
    pr = np.full((len(t1),) + lon2d.shape, 1.0 / 3600.0)  # 1 mm per hour as kg m-2 s-1
    _write(src / "model_pr_2001.nc", t1, {"pr": pr}, {"pr": {"units": "kg m-2 s-1"}}, plev=False)
    return src


def _configure(use_config, src, out):
    return use_config(
        DT=6,
        data_input=str(src),
        output_dir=str(out),
        lat_var="lat",
        lon_var="lon",
        time_var="time",
        pattern_z500="model_z500_*.nc",
        pattern_z200="model_z200_*.nc",
        pattern_z850="model_z850_*.nc",
        pattern_slp="model_psl_*.nc",
        pattern_pr="model_pr_*.nc",
        pattern_ivt="model_ivt_*.nc",
        var_z500="zg",
        var_u200="ua",
        var_v200="va",
        var_u850="ua",
        var_v850="va",
        var_t850="ta",
        var_q850="hus",
        var_msl="psl",
        var_pr="pr",
    )


def test_check_then_prepare_then_track(tmp_path, use_config):
    src = _source(tmp_path)
    out = tmp_path / "prepared"
    _configure(use_config, src, out)

    reports = {r.stream: r for r in check_all()}
    assert overall_status(list(reports.values())) == PREPARE
    z = reports["z500"]
    assert z.status == PREPARE
    assert any("0..360" in n for n in z.notes)
    assert any("x9.81" in n for n in z.notes)
    assert any("squeezed" in n for n in z.notes)
    assert reports["slp"].status == PREPARE and any(
        "subsample 3" in n for n in reports["slp"].notes
    )
    assert reports["pr"].status == PREPARE and any("accumulate 1" in n for n in reports["pr"].notes)
    assert reports["ivt"].status == "missing"
    assert check_cli.main([]) == 1  # not ready yet

    written = prepare_year(2001)
    assert set(written) == {"z500", "z200", "z850", "slp", "pr"}

    with xr.open_dataset(out / "atmotrack_z500_2001.nc") as ds:
        assert ds["z"].dims == ("time", "latitude", "longitude")
        lon = ds["longitude"].values
        assert lon.min() >= -180 and lon.max() <= 180 and np.all(np.diff(lon) > 0)
        assert ds["z"].attrs["units"] == "m**2 s**-2"
        assert abs(float(ds["z"].max()) / 9.81 - 5_400.0) < 1.0
        assert ds["time"].encoding.get("calendar") == "noleap"
    with xr.open_dataset(out / "atmotrack_slp_2001.nc") as ds:
        assert ds.sizes["time"] == 28  # 3 h -> 6 h
        assert abs(float(ds["msl"].mean()) - 101_325.0) < 1.0
    with xr.open_dataset(out / "atmotrack_z850_2001.nc") as ds:
        assert abs(float(ds["t"].mean()) - 278.15) < 0.01
        assert abs(float(ds["q"].mean()) - 0.005) < 1e-6
    with xr.open_dataset(out / "atmotrack_pr_2001.nc") as ds:
        assert ds.sizes["time"] == 28
        assert abs(float(ds["tp"].mean()) - 6.0) < 1e-3  # 6 mm per 6 h window
        assert ds["tp"].attrs["units"] == "mm"

    # prepared files are "ready" with the canonical [data_source] block
    use_config(
        DT=6,
        data_input=str(out),
        lat_var="latitude",
        lon_var="longitude",
        time_var="time",
        pattern_z500="atmotrack_z500_*.nc",
        pattern_z200="atmotrack_z200_*.nc",
        pattern_z850="atmotrack_z850_*.nc",
        pattern_slp="atmotrack_slp_*.nc",
        pattern_pr="atmotrack_pr_*.nc",
        pattern_ivt="atmotrack_ivt_*.nc",
    )
    reports = check_all()
    assert overall_status(reports) == READY, [(r.stream, r.notes) for r in reports]

    # and the trackers run on them through the command-line workers
    from atmotrack.cli._common import load_precip_stream, load_stream
    from atmotrack.cli.col import cutofflow_tracking
    from atmotrack.cli.cy_slp import slp_tracking

    cfg.data_tracking = str(tmp_path / "tracking")  # type: ignore[attr-defined]
    (tmp_path / "tracking").mkdir()
    s = load_stream("pattern_z500", 2001)
    assert len(s.times) == 28 and s.field("var_z500").shape == (28, len(LAT), len(LON))
    p = load_precip_stream("pattern_pr", 2001)
    total, peak = p.accumulated("var_pr", "tp")
    assert total.shape == (28, len(LAT), len(LON)) and abs(total.mean() - 6.0) < 1e-3
    cutofflow_tracking(2001)
    slp_tracking(2001)
    with xr.open_dataset(tmp_path / "tracking" / "col_z500_2001.nc") as ds:
        assert ds["col_objects"].dims == ("time", "latitude", "longitude")
        assert ds["cy_z500_objects"].max() > 0


def test_prepare_cli_writes_config(tmp_path, use_config):
    src = _source(tmp_path)
    out = tmp_path / "prepared"
    _configure(use_config, src, out)
    rc = prepare_cli.main(
        [
            "--years",
            "2001",
            "--streams",
            "z500",
            "--write-config",
            str(tmp_path / "prepared.toml"),
            "-j",
            "1",
        ]
    )
    assert rc == 0
    text = (tmp_path / "prepared.toml").read_text()
    assert 'pattern_z500 = "atmotrack_z500_*.nc"' in text
    assert f'data_input    = "{out.resolve()}"' in text
    assert (out / "atmotrack_z500_2001.nc").is_file()
